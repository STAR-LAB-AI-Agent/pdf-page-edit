"""Create public synthetic PDFs with visible page labels; never overwrite files."""
import argparse
from pathlib import Path
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject


def create(path, count):
    if path.exists():
        print(f"Preserved existing file: {path.name}")
        return
    writer = PdfWriter()
    font = DictionaryObject({NameObject("/Type"):NameObject("/Font"),
                             NameObject("/Subtype"):NameObject("/Type1"),
                             NameObject("/BaseFont"):NameObject("/Helvetica")})
    for index in range(1, count+1):
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):DictionaryObject({NameObject("/F1"):font})})
        content = DecodedStreamObject()
        content.set_data((f"0.1 0.3 0.6 RG 2 w 28 28 539 786 re S\n"
                          f"BT /F1 28 Tf 65 740 Td (PDF Demo {path.stem} - Page {index}/{count}) Tj ET\n"
                          "BT /F1 16 Tf 65 690 Td (Synthetic test data - no personal information) Tj ET\n"
                          f"0.1 0.3 0.6 rg 65 100 {index*60} 80 re f\n").encode("ascii"))
        page[NameObject("/Contents")] = content
    with path.open("xb") as stream:
        writer.write(stream)
    print(f"Created: {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dir", required=True)
    args = parser.parse_args()
    directory = Path(args.dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    create(directory/"1.pdf", 3)
    create(directory/"2.pdf", 2)
