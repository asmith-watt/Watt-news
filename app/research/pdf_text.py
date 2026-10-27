"""Extract text from a PDF. Run as a script so the worker can do it in a throwaway process.

pdfplumber/pdfminer can grow a process by hundreds of MB on a large report, and
CPython rarely hands that memory back, so doing it inside the Celery fork left
the worker dyno over quota for the rest of the research run. The script imports
nothing from the app so the subprocess starts small.

Usage: python -I pdf_text.py <pdf_path> <max_chars>  (text is written to stdout)
"""
import sys

import pdfplumber


def extract_text(path: str, max_chars: int) -> str:
    text_parts = []
    total = 0
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            # Extract tables first
            for table in page.extract_tables():
                for row in table:
                    if row:
                        cells = [str(cell) if cell else '' for cell in row]
                        line = ' | '.join(cells)
                        text_parts.append(line)
                        total += len(line)

            # Extract remaining text
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
                total += len(page_text)

            # pdfplumber caches every parsed object per page; free it
            page.close()

            # The analysis truncates anyway, so don't parse pages it would drop
            if total >= max_chars:
                break

    return '\n\n'.join(text_parts)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stdout.write(extract_text(sys.argv[1], int(sys.argv[2])))
