"""FastAPI application factory."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from tessera import PRODUCT_NAME, __version__
from tessera.api.access import authorize, install_access
from tessera.api.problems import install_problem_handlers
from tessera.api.routers import (
    access,
    branding,
    candidates,
    curation,
    discovery,
    evidence,
    graph,
    groups,
    impact,
    insight,
    merges,
    operations,
    opportunities,
    overview,
    people,
    profiles,
    records,
    saved,
    system,
    teams,
)
from tessera.api.security import install_security_headers
from tessera.api.static import (
    install_api_reference,
    install_branding,
    install_static,
    install_theme,
)
from tessera.auth.sessions import SignInThrottle
from tessera.config import ENV_CONFIG_PATH, Config, load_config
from tessera.home import DataHome
from tessera.lake import Lake
from tessera.ledger.db import make_engine


def create_app(config: Config) -> FastAPI:
    home = DataHome(config.data.home)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        home.require()
        app.state.engine = make_engine(config.ledger_url)
        app.state.lake = Lake(home.lake)
        try:
            yield
        finally:
            app.state.lake.close()
            app.state.engine.dispose()

    app = FastAPI(
        title=f"{PRODUCT_NAME} API",
        version=__version__,
        summary="People, publications, grants, and clinical studies, connected with evidence.",
        lifespan=lifespan,
        # The interactive docs UI loads third-party assets, which the CSP forbids.
        # The OpenAPI document itself is served; a self-hosted reference page comes later.
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        dependencies=[Depends(authorize)],  # every route's role, from access.POLICIES
    )
    app.state.config = config
    app.state.home = home
    app.state.throttle = SignInThrottle()
    install_access(app)  # every request: who is asking, and may they (before the headers,
    install_security_headers(app)  # so that refusals carry them too)
    install_problem_handlers(app)
    for module in (
        system,
        access,
        overview,
        people,
        discovery,
        graph,
        candidates,
        merges,
        records,
        impact,
        opportunities,
        teams,
        profiles,
        curation,
        evidence,
        groups,
        insight,
        saved,
        operations,
        branding,
    ):
        app.include_router(module.router)
    app.include_router(profiles.me_router)
    if config.ai.enabled:  # the optional assistant: nothing of it exists unless turned on
        from tessera.ai.web import install_ai

        install_ai(app)
    install_api_reference(app)
    install_theme(app)
    install_branding(app)
    install_static(app)  # last: its catch-all route must not shadow the API
    return app


def create_app_from_env() -> FastAPI:
    """Factory for ``uvicorn --factory``: reads the config path from ``$TESSERA_CONFIG``."""
    return create_app(load_config(os.environ.get(ENV_CONFIG_PATH)))
