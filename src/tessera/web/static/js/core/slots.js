// Places on a page where optional modules (those /api/v1/meta names) may add their own content.
// A page asks for a slot by name with what it knows; a module provides a function that returns
// nodes (or nothing). With no module providing a slot, a page shows nothing there.

const providers = new Map();

export function provide(name, render) {
  if (!providers.has(name)) providers.set(name, []);
  providers.get(name).push(render);
}

export function slot(name, context = {}) {
  return (providers.get(name) ?? []).flatMap((render) => {
    try {
      return [render(context)].flat();
    } catch (error) {
      console.error(`The ${name} slot failed`, error);
      return [];
    }
  }).filter(Boolean);
}
