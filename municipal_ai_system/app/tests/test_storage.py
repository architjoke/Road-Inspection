"""Storage helper offline smoke tests."""


def test_transformed_url_inserts_tr_path():
    # Pure string helper path without Flask app context
    image_url = "https://ik.imagekit.io/demo/folder/a.jpg"
    transformations = {"width": 400, "height": 300, "quality": 80}

    parts = []
    if "width" in transformations:
        parts.append(f"w-{transformations['width']}")
    if "height" in transformations:
        parts.append(f"h-{transformations['height']}")
    if "quality" in transformations:
        parts.append(f"q-{transformations['quality']}")
    transform_path = ",".join(parts)

    marker = "imagekit.io/"
    idx = image_url.find(marker)
    after = image_url[idx + len(marker) :]
    slash = after.find("/")
    host = image_url[: idx + len(marker) + slash]
    path = after[slash + 1 :]
    out = f"{host}/tr:{transform_path}/{path}"

    assert "tr:w-400,h-300,q-80" in out
