# ===== canonical_schema.py =====

import re


# =========================
# HELPERS
# =========================

def canonical_object_token(name: str):
    if not name:
        return ""

    return re.sub(
        r"[^a-z0-9]",
        "",
        name.lower()
    )


def canonical_pk_name(object_name: str):
    token = canonical_object_token(object_name)
    return f"{token}id"


def canonical_fk_name(object_name: str):
    token = canonical_object_token(object_name)
    return f"ref_{token}"


# =========================
# APPLY CANONICALIZATION
# =========================

def apply_canonical_schema(schema: dict):

    if not schema:
        return schema

    object_names = {}

    # =========================
    # REGISTER OBJECTS
    # =========================

    for obj in schema.get("objects", []):
        obj_name = obj.get("name")

        if not obj_name:
            continue

        token = canonical_object_token(obj_name)
        object_names[token] = obj_name

    # =========================
    # FIX OBJECT FIELDS
    # =========================

    for obj in schema.get("objects", []):
        obj_name = obj.get("name")

        if not obj_name:
            continue

        pk_name = canonical_pk_name(obj_name)

        new_fields = []
        seen = set()
        pk_added = False

        for field in obj.get("fields", []):
            field_name = field.get("name")

            if not field_name:
                continue

            field_name = field_name.strip().lower()
            normalized_field = canonical_object_token(field_name)

            # =========================
            # PRIMARY KEY DETECTION
            # =========================

            if normalized_field.endswith("id"):

                object_token = canonical_object_token(obj_name)

                if object_token in normalized_field:

                    if not pk_added:
                        if pk_name not in seen:
                            new_fields.append({
                                "name": pk_name,
                                "type": "integer"
                            })
                            seen.add(pk_name)

                        pk_added = True

                    continue

            # =========================
            # FOREIGN KEY 
            # =========================

            if field_name.startswith("ref_"):

                fk_target = field_name.replace("ref_", "").lower()
                matched = False

                for token, real_name in object_names.items():

                
                    if fk_target == token or fk_target.endswith(token):

                        canonical_name = canonical_fk_name(real_name)

                        if canonical_name not in seen:
                            new_fields.append({
                                "name": canonical_name,
                                "type": "integer"  
                            })
                            seen.add(canonical_name)

                        matched = True
                        break

                if matched:
                    continue

            # =========================
            # REMOVE DUPLICATES
            # =========================

            if field_name in seen:
                continue

            seen.add(field_name)

            new_fields.append({
                "name": field_name,
                "type": field.get("type", "string")
            })

        # =========================
        # ENSURE PK EXISTS
        # =========================

        if not pk_added:
            if pk_name not in seen:
                new_fields.insert(0, {
                    "name": pk_name,
                    "type": "integer"
                })

        obj["fields"] = new_fields

    print("[canonical_schema] canonicalização aplicada")

    return schema
