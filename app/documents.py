import subprocess
import tempfile
from pathlib import Path
import pymupdf

class DocumentError(Exception):
    pass

def extract_pdf(raw, max_pages=50, max_ocr_pages=8):
    pages, ocr_count = [], 0
    try:
        with pymupdf.open(stream=raw, filetype='pdf') as doc, tempfile.TemporaryDirectory() as tmp:
            if len(doc) > max_pages:
                raise DocumentError('PDF překračuje limit 50 stran; vyžaduje ruční kontrolu.')
            for i, page in enumerate(doc):
                text = page.get_text()
                if len(text.strip()) < 40:
                    ocr_count += 1
                    if ocr_count > max_ocr_pages:
                        raise DocumentError('PDF překračuje limit 8 OCR stran.')
                    image = Path(tmp) / 'page.png'
                    if page.rect.width * page.rect.height > 2_000_000:
                        raise DocumentError('Nestandardně velká stránka PDF.')
                    page.get_pixmap(dpi=200).save(image)
                    try:
                        r = subprocess.run(['tesseract', str(image), 'stdout', '-l', 'ces'], capture_output=True, text=True, timeout=60)
                    except (OSError, subprocess.TimeoutExpired) as e:
                        raise DocumentError('OCR není dostupné nebo překročilo časový limit.') from e
                    if r.returncode:
                        raise DocumentError('OCR selhalo; ověřte instalaci českého modelu ces.')
                    text = r.stdout
                if len(text.strip()) < 40:
                    raise DocumentError('Stránku nelze spolehlivě přečíst.')
                pages.append({'page': i + 1, 'text': text})
    except DocumentError:
        raise
    except Exception as e:
        raise DocumentError('PDF nelze bezpečně přečíst.') from e
    return pages
