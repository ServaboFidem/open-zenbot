// Settings > Tracing: the page's forms and labels. The page never holds the Langfuse secret key: GET
// gives its last four characters, and a blank secret on save keeps the one in force.

export const SOURCE_LABELS = { saved: 'Saved here', '.env': 'From .env', off: 'Off' };

export function sourceLabel(section) {
  if (!section) return '';
  if (section.source === 'saved' && !(section.dsn ?? section.public_key)) return 'Turned off here';
  return SOURCE_LABELS[section.source] || section.source;
}

export const sentryForm = s => ({ dsn: s?.sentry?.dsn || '' });

export const langfuseForm = s => {
  const lf = s?.langfuse || {};
  return { public_key: lf.public_key || '', secret_key: '', base_url: lf.base_url || '', environment: lf.environment || '' };
};

/** What a Langfuse save sends: trimmed, and no secret_key at all when left blank (keep the one in force). */
export function langfuseBody(form) {
  const body = {};
  for (const k of ['public_key', 'base_url', 'environment']) body[k] = (form[k] || '').trim();
  const secret = (form.secret_key || '').trim();
  if (secret) body.secret_key = secret;
  return body;
}

export function secretHint(lf) {
  const h = lf?.secret_key_hint;
  if (!h) return 'No secret key set';
  return h === 'set' ? 'Secret key set' : `Secret key set · ends in …${h}`;
}

export const sentryDirty = (s, form) => form.dsn.trim() !== (s?.sentry?.dsn || '');

export const langfuseDirty = (s, form) => {
  const lf = s?.langfuse || {};
  return ['public_key', 'base_url', 'environment'].some(k => (form[k] || '').trim() !== (lf[k] || '')) || !!form.secret_key.trim();
};
