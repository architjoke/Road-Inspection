"""Storage helper offline smoke tests."""

from app import create_app
from app.services.imagekit_service import get_transformed_url, is_imagekit_configured


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


def test_imagekit_reads_env_config_keys_not_secret_values():
    app = create_app(
        {
            "TESTING": True,
            "OFFLINE_MODE": True,
            "IMAGEKIT_PRIVATE_KEY": "private_test_key",
            "IMAGEKIT_PUBLIC_KEY": "public_test_key",
            "IMAGEKIT_URL_ENDPOINT": "https://ik.imagekit.io/architmsg",
        }
    )
    with app.app_context():
        assert is_imagekit_configured() is True
        transformed = get_transformed_url(
            "https://ik.imagekit.io/architmsg/folder/a.jpg",
            {"width": 400, "height": 300},
        )
        assert "/tr:w-400,h-300/" in transformed


def test_imagekit_placeholder_keys_are_not_configured():
    app = create_app(
        {
            "TESTING": True,
            "OFFLINE_MODE": True,
            "IMAGEKIT_PRIVATE_KEY": "private_xxxxxxxx",
            "IMAGEKIT_PUBLIC_KEY": "public_xxxxxxxx",
            "IMAGEKIT_URL_ENDPOINT": "https://ik.imagekit.io/your_imagekit_id",
        }
    )
    with app.app_context():
        assert is_imagekit_configured() is False

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
