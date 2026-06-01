path = 'stock_take/templates/stock_take/ordering.html'
with open(path, encoding='utf-8') as f:
    content = f.read()

# The file has UTF-8 bytes that were re-interpreted as Windows-1252 and re-encoded as UTF-8.
# Fix each known bad sequence by encoding as cp1252 to recover the original bytes,
# then decoding those bytes as UTF-8 to get the correct character.
def mojibake_to_correct(s):
    return s.encode('cp1252').decode('utf-8')

bad_sequences = [
    '\u00e2\u0153\u201c',   # âœ" (cp1252 bytes E2 9C 93) -> ✓ U+2713
    '\u00e2\u0153\u2014',   # âœ— (cp1252 bytes E2 9C 97) -> ✗ U+2717
    '\u00e2\u0153\u2022',   # âœ• (cp1252 bytes E2 9C 95) -> ✕ U+2715
    '\u00e2\u2020\u2018',   # â†' (cp1252 bytes E2 86 91) -> ↑ U+2191
    '\u00e2\u2020\u201c',   # â†" (cp1252 bytes E2 86 93) -> ↓ U+2193
    '\u00e2\u20ac\u201d',   # â€" (cp1252 bytes E2 80 94) -> — U+2014
    '\u00c2\u00a3',         # Â£  (cp1252 bytes C2 A3)    -> £ U+00A3
    '\u00c2\u00b7',         # Â·  (cp1252 bytes C2 B7)    -> · U+00B7
    '\u00e2\u201d\u20ac',   # â"€ (cp1252 bytes E2 94 80) -> ─ U+2500
]

for bad in bad_sequences:
    try:
        good = mojibake_to_correct(bad)
    except Exception as e:
        print(f'  SKIP {repr(bad)}: {e}')
        continue
    count = content.count(bad)
    if count:
        print(f'  {repr(bad)} -> {repr(good)}: {count}x')
    content = content.replace(bad, good)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
print('Done')
