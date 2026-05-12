import zipfile
import xml.etree.ElementTree as ET

def read_docx(path):
    with zipfile.ZipFile(path) as docx:
        tree = ET.XML(docx.read('word/document.xml'))
    text = []
    for paragraph in tree.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
        texts = [node.text for node in paragraph.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t') if node.text]
        if texts:
            text.append(''.join(texts))
    return '\n'.join(text)

with open('doc_content.txt', 'w', encoding='utf-8') as f:
    f.write(read_docx('AI LCA modelling - System Prompt 1.docx'))
