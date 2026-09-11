"""Compatibility shim — storage is ImageKit via imagekit_service."""

from app.services.imagekit_service import (
    delete_from_imagekit,
    get_image_details,
    get_transformed_url,
    is_imagekit_configured,
    upload_to_imagekit,
)

__all__ = [
    "upload_to_imagekit",
    "get_image_details",
    "get_transformed_url",
    "delete_from_imagekit",
    "is_imagekit_configured",
]
