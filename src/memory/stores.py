"""Store names: the vocabulary every memory module shares."""

import hashlib
import re

PERSONAL = "personal"
WORLD = "world"
SHARED_TAG = "shared"


def org_store(slug: str) -> str:
    return "org:" + slug


def is_org(store: str) -> bool:
    return store.startswith("org:")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if slug or not name.strip():
        return slug
    # nothing ASCII survived (a name in another script): a short stable hash keeps such names apart
    return "org_" + hashlib.md5(" ".join(name.lower().split()).encode("utf-8")).hexdigest()[:8]
