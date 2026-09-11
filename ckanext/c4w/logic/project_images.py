"""Verified project images with matching browser/server crop positioning."""
from io import BytesIO
import secrets
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage
import ckan.plugins.toolkit as tk

from ckanext.c4w import constants
from ckanext.c4w.logic import uploads


def store_image(upload, field, options):
    limit = int(tk.config.get('ckan.max_image_size') or 2)
    try:
        if upload is None:
            raise ValueError('Choose an image.')
        raw = upload.read(limit * 1024 * 1024 + 1)
        if len(raw) > limit * 1024 * 1024:
            raise ValueError('Image exceeds the maximum size of %s MB.' % limit)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as probe:
                if probe.format not in ('PNG', 'JPEG', 'WEBP'):
                    raise ValueError('Use a PNG, JPEG or WebP image.')
                probe.verify()
            with Image.open(BytesIO(raw)) as source:
                rgba = ImageOps.exif_transpose(source).convert('RGBA')
                source = Image.new('RGB', rgba.size, 'white')
                source.paste(rgba, mask=rgba.getchannel('A'))
                _, width, height = constants.PROJECT_IMAGE_BOXES[field]
                x = max(0, min(1, float(options.get('crop_x', 0.5))))
                y = max(0, min(1, float(options.get('crop_y', 0.5))))
                cropped = ImageOps.fit(source, (width, height), centering=(x, y))
                result = BytesIO()
                cropped.save(result, format='JPEG', quality=90)
        result.seek(0)
        name = 'project-%s-%s.jpg' % (field, secrets.token_hex(12))
        fresh = FileStorage(result, filename=name, content_type='image/jpeg')
        url, _, cleanup = uploads._store(fresh, name, 'image/jpeg', limit)
        return url, cleanup
    except (ValueError, TypeError, OSError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning, uploads.UploadError) as exc:
        raise tk.ValidationError({field: [tk._(str(exc))]})
