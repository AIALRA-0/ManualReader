"""Give consumers actual pixels, original text or source-backed structure."""

from base64 import b64encode
from io import BytesIO
import json

from PIL import Image, ImageChops


MAX_PIXELS = 36_000_000


def pixels(raw):
    with Image.open(BytesIO(raw)) as picture:
        if picture.width < 2 or picture.height < 2 or picture.width * picture.height > MAX_PIXELS:
            raise ValueError('原件的像素尺寸超过可读取范围')
        picture.verify()
    with Image.open(BytesIO(raw)) as picture:
        from PIL import ImageOps
        picture = ImageOps.exif_transpose(picture).convert('RGBA')
        canvas = Image.new('RGBA', picture.size, 'white')
        canvas.alpha_composite(picture)
        image = canvas.convert('RGB')
    if ImageChops.difference(image, Image.new('RGB', image.size, image.getpixel((0,0)))).getbbox() is None:
        raise ValueError('画面只有单一颜色，未发现可读内容')
    output = BytesIO()
    image.save(output, format='PNG')
    return output.getvalue(), list(image.size)


def original_text(kind, locator, text, **features):
    return {'kind':kind, 'locator':locator, 'status':'available' if text else 'empty',
            'text':text, 'capabilities':['read_text','navigate_origin'] if text else ['navigate_origin'], **features}


def visual(cache, kind, locator, raw, **features):
    try:
        image, size = pixels(raw)
        return {'kind':kind, 'locator':locator, 'status':'available',
                'capabilities':['inspect_visual','navigate_origin'],
                'carrier':{'sha256':cache.blob(image),'mime':'image/png','size':size}, **features}
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        return unavailable(kind,locator,str(error),**features)


def unavailable(kind, locator, reason, **features):
    return {'kind':kind, 'locator':locator, 'status':'unavailable', 'reason':reason,
            'capabilities':['navigate_origin'], **features}


def message(cache, asset, capability):
    if capability not in asset['capabilities']:
        raise ValueError('这份原件资产没有所需的可读取载体')
    if capability == 'inspect_visual':
        expected = asset['carrier']['sha256']
        raw = cache.carrier(expected)
        # The persistent carrier is already normalized and hashed when it is registered.
        with Image.open(BytesIO(raw)) as image:
            if (image.format != 'PNG' or image.mode != 'RGB'
                    or image.width < 2 or image.height < 2 or image.width*image.height > MAX_PIXELS
                    or list(image.size) != asset['carrier']['size']):
                raise ValueError('载体格式或尺寸与登记不同')
            image.verify()
        block = {'type':'input_image','image_url':'data:image/png;base64,'+b64encode(raw).decode('ascii')}
    elif capability == 'read_text':
        block = {'type':'input_text','text':asset['text']}
    elif capability == 'compute_structure':
        block = {'type':'input_text','text':json.dumps(asset['structure'],ensure_ascii=False)}
    else:
        block = {'type':'input_text','text':json.dumps(asset['locator'],ensure_ascii=False)}
    return block
