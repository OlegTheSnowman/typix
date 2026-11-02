"""Convert Markdown files in doc/ to HTML for NVDA addon packaging.
Usage: python tools/md2html.py
Scans doc/**/readme.md and produces readme.html alongside each."""

import os
import re
import sys


def md_to_html(md_text):
    """Minimal Markdown to HTML converter. Handles common elements."""
    lines = md_text.split('\n')
    html_lines = []
    in_code = False
    in_ul = False
    in_ol = False
    in_table = False
    table_header_done = False

    for line in lines:
        stripped = line.rstrip()

        # Fenced code blocks
        if stripped.startswith('```'):
            if in_code:
                html_lines.append('</code></pre>')
                in_code = False
            else:
                lang = stripped[3:].strip()
                cls = f' class="language-{lang}"' if lang else ''
                html_lines.append(f'<pre><code{cls}>')
                in_code = True
            continue

        if in_code:
            html_lines.append(_escape(stripped))
            continue

        # Blank line closes lists/tables
        if not stripped:
            if in_ul:
                html_lines.append('</ul>')
                in_ul = False
            if in_ol:
                html_lines.append('</ol>')
                in_ol = False
            if in_table:
                html_lines.append('</table>')
                in_table = False
                table_header_done = False
            html_lines.append('')
            continue

        # Table rows
        if '|' in stripped and stripped.startswith('|'):
            cells = [c.strip() for c in stripped.strip('|').split('|')]
            # Skip separator row (---|----|---)
            if all(re.match(r'^-+:?$|^:?-+:?$', c) for c in cells):
                continue
            if not in_table:
                html_lines.append('<table>')
                in_table = True
                tag = 'th'
                table_header_done = False
            elif not table_header_done:
                tag = 'th'
                table_header_done = True
            else:
                tag = 'td'
            row = ''.join(f'<{tag}>{_inline(c)}</{tag}>' for c in cells)
            html_lines.append(f'<tr>{row}</tr>')
            if tag == 'th':
                table_header_done = True
            continue

        # Close table if we hit non-table content
        if in_table:
            html_lines.append('</table>')
            in_table = False
            table_header_done = False

        # Headers
        m = re.match(r'^(#{1,6})\s+(.*)', stripped)
        if m:
            if in_ul:
                html_lines.append('</ul>'); in_ul = False
            level = len(m.group(1))
            html_lines.append(f'<h{level}>{_inline(m.group(2))}</h{level}>')
            continue

        # Unordered list
        m = re.match(r'^[\*\-]\s+(.*)', stripped)
        if m:
            if not in_ul:
                html_lines.append('<ul>')
                in_ul = True
            html_lines.append(f'<li>{_inline(m.group(1))}</li>')
            continue

        # Ordered list
        m = re.match(r'^\d+\.\s+(.*)', stripped)
        if m:
            if not in_ol:
                html_lines.append('<ol>')
                in_ol = True
            html_lines.append(f'<li>{_inline(m.group(1))}</li>')
            continue

        # Close lists if needed
        if in_ul:
            html_lines.append('</ul>'); in_ul = False
        if in_ol:
            html_lines.append('</ol>'); in_ol = False

        # Horizontal rule
        if re.match(r'^---+$', stripped):
            html_lines.append('<hr>')
            continue

        # Paragraph
        html_lines.append(f'<p>{_inline(stripped)}</p>')

    # Close any open elements
    if in_ul:
        html_lines.append('</ul>')
    if in_ol:
        html_lines.append('</ol>')
    if in_table:
        html_lines.append('</table>')
    if in_code:
        html_lines.append('</code></pre>')

    return '\n'.join(html_lines)


def _escape(text):
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _inline(text):
    """Process inline markdown: bold, italic, code, links."""
    text = _escape(text)
    # Code spans (must be first to avoid processing inside them)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # Bold + italic
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    # Bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    # Italic
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    return text


def convert_doc_tree(doc_dir):
    """Find all readme.md in doc/ and convert to readme.html."""
    count = 0
    for root, dirs, files in os.walk(doc_dir):
        for fname in files:
            if fname.lower().endswith('.md'):
                md_path = os.path.join(root, fname)
                html_name = os.path.splitext(fname)[0] + '.html'
                html_path = os.path.join(root, html_name)

                with open(md_path, 'r', encoding='utf-8') as f:
                    md_text = f.read()

                # Extract title from first H1
                title_match = re.search(r'^#\s+(.+)', md_text, re.MULTILINE)
                title = title_match.group(1) if title_match else 'Documentation'

                body = md_to_html(md_text)
                # The folder is the language, so screen readers pick the
                # right voice for each help file.
                lang = os.path.basename(root)
                html = f'''<!DOCTYPE html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<title>{_escape(title)}</title>
<link rel="stylesheet" href="../style.css" type="text/css">
</head>
<body>
{body}
</body>
</html>'''
                with open(html_path, 'w', encoding='utf-8') as f:
                    f.write(html)
                count += 1
                print(f"  {os.path.relpath(html_path, doc_dir)}")
    return count


if __name__ == '__main__':
    doc_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'doc')
    if not os.path.isdir(doc_dir):
        print('No doc/ directory found.')
        sys.exit(1)
    print('Converting markdown to HTML...')
    n = convert_doc_tree(doc_dir)
    print(f'Converted {n} file(s).')
