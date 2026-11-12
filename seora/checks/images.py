"""Images: alt text, broken images, file size, format, width/height and lazy loading.

Each image is fetched once (a HEAD request, or a 1-byte ranged GET when HEAD gives no size)
through ctx.fetch, a few at a time, within config.ASSET_BUDGET seconds.
"""

import base64
import posixpath
import re
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit

import config

from ._fetchmany import fetch_many
from ._sizes import probe_size
from ._issue import clip, issue

GROUP = "Images"

LARGE_BYTES = 200_000       # bigger than this is a large image
FORMAT_MIN_BYTES = 10_000   # don't suggest a new format for tiny files such as icons
EAGER_FIRST = 3             # the first few images are probably on the first screen: don't ask to lazy-load them
TOP_IMAGES = 2              # lazy loading these delays what visitors see first
EXAMPLES = 5

OLD_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/gif": "GIF", "image/bmp": "BMP", "image/tiff": "TIFF"}
MODERN = {"image/webp", "image/avif"}
BY_EXTENSION = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jfif": "image/jpeg", ".png": "image/png",
                ".gif": "image/gif", ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff",
                ".webp": "image/webp", ".avif": "image/avif", ".svg": "image/svg+xml", ".ico": "image/x-icon"}
ACCEPT = "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"   # what browsers send, so servers that pick a format answer as for a visitor
LAZY_SRC = ("data-src", "data-lazy-src", "data-original", "data-lazy")


@dataclass
class Img:
    n: int                 # position on the page, from 1
    attrs: dict
    url: str               # address the browser ends up loading ("" when there is none)
    js_lazy: bool          # loaded later by a script (data-src and a placeholder src)
    name: str              # short name for reports
    status: int = None     # HTTP status, None when not fetched or no answer
    size: int = None       # bytes
    ctype: str = ""
    fetched: bool = False  # a fetch was attempted


def run(ctx):
    imgs = collect(ctx.page)
    if not imgs:
        return [issue("images-none", "pass", "Page has no images",
                      "No <img> tags were found. Images set as CSS backgrounds aren't checked.")]
    skipped = measure(ctx, imgs)
    return (alt_text(imgs) + broken(imgs, skipped) + sizes(imgs, skipped) + formats(imgs)
            + dimensions(imgs) + lazy_loading(imgs))


# ---------- gathering ----------

def _name(url):
    if url.startswith("data:"):
        return "embedded image"
    try:
        p = urlsplit(url)
    except ValueError:
        return clip(url, 40)
    base = unquote(posixpath.basename(p.path.rstrip("/"))) or p.hostname or url
    return clip(base, 40)


def _px(value):
    m = re.match(r"\s*(\d+)", value or "")
    return int(m.group(1)) if m else None


def collect(page):
    base = page.base_href or page.url
    out = []
    for a in page.images:
        # Tracking pixels (1×1 or 0×0) aren't content: leave them out of every check
        w, h = _px(a.get("width")), _px(a.get("height"))
        if (w is not None and w <= 1) or (h is not None and h <= 1):
            continue
        src = (a.get("src") or "").strip()
        later = next(((a.get(k) or "").strip() for k in LAZY_SRC if (a.get(k) or "").strip()), "")
        js_lazy = bool(later) and (not src or src.startswith("data:"))
        chosen = later if js_lazy else src
        try:
            url = urljoin(base, chosen) if chosen else ""
        except ValueError:
            url = ""
        out.append(Img(n=len(out) + 1, attrs=a, url=url, js_lazy=js_lazy, name=_name(url or chosen) or "image without address"))
    return out


def _examples(imgs, label=lambda i: i.name):
    names = list(dict.fromkeys(label(i) for i in imgs))
    shown = ", ".join(names[:EXAMPLES])
    return shown + (f" and {len(names) - EXAMPLES} more" if len(names) > EXAMPLES else "")


def _kb(n):
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{max(1, round(n / 1000))} KB"


def _plural(n, word):
    if n == 1:
        return f"1 {word}"
    return f"{n} {word}{'es' if word.endswith(('s', 'sh', 'ch', 'x')) else 's'}"


# ---------- fetching ----------

def image_type(ct, url):
    """The image format: from the server's Content-Type, or from the file extension when that says nothing useful."""
    if ct.startswith("image/"):
        return "image/jpeg" if ct == "image/jpg" else ct
    path = urlsplit(url).path.lower()
    return BY_EXTENSION.get(posixpath.splitext(path)[1], ct)


def probe(ctx, url):
    """(status, size, image type) for one image. status None means no answer."""
    status, size, ct = probe_size(ctx, url, ACCEPT)
    return status, size, (image_type(ct, url) if status is not None and status < 400 else "")


def _data_url_size(url):
    head, _, data = url.partition(",")
    if ";base64" in head:
        return len(data) * 3 // 4
    return len(unquote(data).encode("utf-8"))


def measure(ctx, imgs):
    """Fetch each distinct image once. Returns how many distinct images were left unmeasured because of the cap."""
    for i in imgs:
        if i.url.startswith("data:"):
            i.size = _data_url_size(i.url)
            i.ctype = i.url[5:].split(";")[0].split(",")[0].lower()
            i.status = 200
    urls = [i.url for i in imgs if i.url.startswith(("http://", "https://"))]
    results, attempted, skipped = fetch_many(ctx, urls, probe, config.MAX_IMAGES_CHECKED)
    for i in imgs:
        i.fetched = i.url in attempted
        if i.url in results:
            i.status, i.size, i.ctype = results[i.url] or (None, None, "")
    return skipped


# ---------- checks ----------

def _beyond_cap(skipped, verb):
    if not skipped:
        return ""
    was = "wasn't" if skipped == 1 else "weren't"
    return f" {skipped} more {was} {verb} (limit {config.MAX_IMAGES_CHECKED})."


def _decorative(i):
    return (i.attrs.get("role") or "").lower() in ("presentation", "none") or (i.attrs.get("aria-hidden") or "").lower() == "true"


def _labelled(i):
    # aria-label / aria-labelledby give the image a description that screen readers read out
    return bool((i.attrs.get("aria-label") or "").strip() or (i.attrs.get("aria-labelledby") or "").strip())


def alt_text(imgs):
    missing = [i for i in imgs if "alt" not in i.attrs and not _decorative(i) and not _labelled(i)]
    empty = [i for i in imgs if "alt" in i.attrs and not i.attrs["alt"].strip()]
    out = []
    if missing:
        out.append(issue("img-alt-missing", "warning", f"{_plural(len(missing), 'image')} without alt text",
                         f"{_examples(missing)}. Alt text describes an image to search engines (it helps it appear in image search) and to people using screen readers.",
                         'Add an alt attribute that says what each image shows, e.g. alt="Red running shoe, side view". For purely decorative images, use alt="".'))
    if empty:
        out.append(issue("img-alt-empty", "notice", f"{_plural(len(empty), 'image')} with empty alt text",
                         f"{_examples(empty)}. Empty alt (alt=\"\") is right for decorative images, which screen readers then skip, but images that show content need a description.",
                         "Check these images: if one shows something meaningful (a product, a person, a chart), describe it in its alt text."))
    if not out:
        out.append(issue("img-alt-ok", "pass", "The image has alt text" if len(imgs) == 1 else f"All {len(imgs)} images have alt text"))
    return out


def broken(imgs, skipped):
    seen, bad, unanswered = set(), [], []
    for i in imgs:
        if i.url in seen or not i.url.startswith(("http://", "https://")):
            continue
        seen.add(i.url)
        if i.status is not None and i.status >= 400:
            bad.append(i)
        elif i.fetched and i.status is None:
            unanswered.append(i)
    out = []
    if bad:
        out.append(issue("img-broken", "error", f"{_plural(len(bad), 'image')} don't load" if len(bad) > 1 else "1 image doesn't load",
                         _examples(bad, lambda i: f"{i.name} ({i.status})") + ". Visitors see a broken image and search engines can't index it.",
                         "Upload the missing files or correct the image addresses."))
    if unanswered:
        out.append(issue("img-unchecked", "notice", f"{_plural(len(unanswered), 'image')} couldn't be checked",
                         f"{_examples(unanswered)} didn't answer in time, so their size and format are unknown.",
                         "Open these images in a browser to make sure they load, and check that the server answers quickly."))
    if not out and seen:
        out.append(issue("img-load-ok", "pass", "All images load",
                         f"{_plural(len(seen), 'image address')} checked." + _beyond_cap(skipped, "checked")))
    return out


def sizes(imgs, skipped):
    unique = list({i.url: i for i in imgs if i.size}.values())
    if not unique:
        return []
    large = sorted((i for i in unique if i.size > LARGE_BYTES), key=lambda i: -i.size)
    total = sum(i.size for i in unique)
    biggest = max(unique, key=lambda i: i.size)
    note = _beyond_cap(skipped, "measured")
    if large:
        with_srcset = sum(1 for i in large if (i.attrs.get("srcset") or i.attrs.get("data-srcset") or "").strip())
        srcset_note = (f" {with_srcset} of them offer smaller versions (srcset), so browsers on small screens may download less; "
                       "the size shown is the main file." if with_srcset else "")
        return [issue("img-large", "warning", f"{_plural(len(large), 'image')} larger than {_kb(LARGE_BYTES)}",
                      _examples(large, lambda i: f"{i.name} ({_kb(i.size)})")
                      + f". Large images are the most common reason pages load slowly, especially on phones. All images together: {_kb(total)}.{note}{srcset_note}",
                      "Resize each image to the largest size it's shown at, compress it, and use WebP or AVIF. Free tools such as Squoosh do all three.")]
    return [issue("img-size-ok", "pass", "Image file sizes are fine",
                  f"{_plural(len(unique), 'image')} measured, {_kb(total)} in total. Largest: {biggest.name} ({_kb(biggest.size)}).{note}")]


def formats(imgs):
    seen, old = set(), []
    for i in imgs:
        if i.url in seen or not i.size:
            continue
        seen.add(i.url)
        offered = i.attrs.get("_picture_types") or []
        if i.ctype in OLD_FORMATS and i.size >= FORMAT_MIN_BYTES and not (MODERN & set(offered)):
            old.append(i)
    if not seen:
        return []
    if old:
        old.sort(key=lambda i: -i.size)
        return [issue("img-format", "notice", f"{_plural(len(old), 'image')} could use WebP or AVIF",
                      _examples(old, lambda i: f"{i.name} ({OLD_FORMATS[i.ctype]}, {_kb(i.size)})")
                      + ". WebP and AVIF files are usually 25–50% smaller than JPEG or PNG at the same quality, and every current browser shows them.",
                      "Save these images as WebP or AVIF. To keep a fallback for very old browsers, use <picture> with a <source type=\"image/webp\">.")]
    return [issue("img-format-ok", "pass", "Images use modern formats",
                  f"No image over {_kb(FORMAT_MIN_BYTES)} is in JPEG, PNG or GIF without a WebP or AVIF version.")]


def dimensions(imgs):
    missing = [i for i in imgs if not ((i.attrs.get("width") or "").strip() and (i.attrs.get("height") or "").strip())]
    if missing:
        return [issue("img-dimensions", "notice", f"{_plural(len(missing), 'image')} without width and height",
                      f"{_examples(missing)}. Without them the browser can't keep space for the image, so the text jumps as it loads. "
                      "Google measures this as layout shift (CLS). If your CSS already sets the image's aspect ratio, you can ignore this.",
                      'Add width and height attributes with each image\'s real size in pixels, e.g. <img width="800" height="600">. '
                      "Add height: auto in CSS so it still scales on small screens.")]
    return [issue("img-dimensions-ok", "pass", "All images have width and height set")]


def _lazy(i):
    return (i.attrs.get("loading") or "").strip().lower() == "lazy" or i.js_lazy


def lazy_loading(imgs):
    out = []
    late = [i for i in imgs[EAGER_FIRST:] if not _lazy(i)]
    top = [i for i in imgs[:TOP_IMAGES] if (i.attrs.get("loading") or "").strip().lower() == "lazy" or i.js_lazy]
    if top:
        out.append(issue("img-lazy-top", "notice", "The first image on the page is lazy-loaded",
                         f"{_examples(top)}. If this image is on the first screen, lazy loading makes it wait. Images at the top are what visitors see first, "
                         "and often the page's largest element, which Google times as LCP. If it's further down the page, this is fine.",
                         'If the image is on the first screen, remove loading="lazy" (or the lazy-loading script) from it.'))
    if late:
        out.append(issue("img-lazy-missing", "notice", f"{_plural(len(late), 'image')} further down load straight away",
                         f"{_examples(late)}. Images below the first screen are downloaded with the page even if nobody scrolls to them, which slows the first load.",
                         'Add loading="lazy" to images below the first screen. Leave it off the first few images at the top.'))
    if not out:
        detail = (f"Images after the first {EAGER_FIRST} load only when needed." if len(imgs) > EAGER_FIRST
                  else f"With {_plural(len(imgs), 'image')}, lazy loading isn't needed.")
        out.append(issue("img-lazy-ok", "pass", "Lazy loading is set up well", detail))
    return out
