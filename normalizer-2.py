import re


# =========================
# OBJECT NAMES
# =========================

def normalize_object_name(name: str):

    if not name:
        return name

    name = str(name).strip()

    # lowercase helper
    lower = name.lower()

    # remove trailing "id"
    if lower.endswith("id") and len(name) > 3:
        name = name[:-2]

    # remove duplicate spaces/underscores
    name = re.sub(
        r"[_\s]+",
        " ",
        name
    )

    # remove special chars
    name = re.sub(
        r"[^a-zA-Z0-9 ]",
        "",
        name
    )

    # TitleCase
    name = "".join(
        word.capitalize()
        for word in name.split()
    )

    return name


# =========================
# FIELD NAMES
# =========================

def normalize_field_name(name: str):

    if not name:
        return name

    name = str(name).strip().lower()

    # remove duplicate id
    while "idid" in name:

        name = name.replace(
            "idid",
            "id"
        )

    # spaces/dashes -> underscore
    name = re.sub(
        r"[\s\-]+",
        "_",
        name
    )

    # remove special chars
    name = re.sub(
        r"[^a-z0-9_]",
        "",
        name
    )

    return name


# =========================
# WORKSPACE NAMES
# =========================

def normalize_workspace_name(name: str):

    if not name:
        return name

    name = str(name).strip()

    name = re.sub(
        r"[_\s]+",
        " ",
        name
    )

    return " ".join(
        word.capitalize()
        for word in name.split()
    )
