"""Grants.gov's codes, small enough for the configuration to check against."""

# Grants.gov's codes for who may apply (its "eligible applicants"), in short.
APPLICANT_TYPES = {
    "00": "State governments",
    "01": "County governments",
    "02": "City or township governments",
    "04": "Special district governments",
    "05": "Independent school districts",
    "06": "Public and state-controlled institutions of higher education",
    "07": "Federally recognized Native American tribal governments",
    "08": "Public and Indian housing authorities",
    "11": "Native American tribal organizations",
    "12": "Nonprofits with 501(c)(3) status, other than institutions of higher education",
    "13": "Nonprofits without 501(c)(3) status, other than institutions of higher education",
    "20": "Private institutions of higher education",
    "21": "Individuals",
    "22": "For-profit organizations other than small businesses",
    "23": "Small businesses",
    "25": "Others (see the eligibility notes)",
    "99": "Unrestricted (see the eligibility notes)",
}

__all__ = ["APPLICANT_TYPES"]
