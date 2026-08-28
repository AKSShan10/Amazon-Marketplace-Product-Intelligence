"""
scraper/spotcheck.py
====================
Acceptance criterion 7.1:

    "Check 5-10% of product URLs to make sure the information matches the
     actual website. Video links should work."

WHY THIS IS PART MANUAL
-----------------------
The criterion exists to prove a HUMAN confirmed the data matches the live
site. If we verified it by scraping the page again, we would only be testing
the scraper against itself - the same selector bug would produce the same
wrong answer twice and the check would pass.

So the split is:
    this tool   picks a fair random sample and lays out the comparison
    you         open each page and confirm what you see

The HTML sheet makes that fast: scraped values on the left, a link to the live
page on the right, tick boxes, then export your results as CSV.

    python main.py spotcheck              build the sheet
    ... you do the checking ...
    python main.py spotcheck --summary    read results back, print pass rate
"""

import csv
import html as html_escape
import json
import random

import config
from scraper.logs import get_logger
from scraper.storage import Storage

log = get_logger()

SAMPLE_CSV = config.DATA_DIR / "spotcheck.csv"
SHEET_HTML = config.DATA_DIR / "spotcheck.html"
RESULT_CSV = config.DATA_DIR / "spotcheck_results.csv"

# The fields a reviewer would actually eyeball on the page
CHECK_FIELDS = ["title", "brand", "price", "rating", "review_count"]

COLUMNS = (
    ["asin", "product_url"]
    + [f"scraped_{f}" for f in CHECK_FIELDS]
    + [f"matches_{f}" for f in CHECK_FIELDS]
    + ["image_opens", "video_link_works", "notes"]
)


def build(percent=8, seed=42):
    """
    Pick a random sample and write two files:

        data/spotcheck.csv   the evidence table (blank tick columns)
        data/spotcheck.html  a sheet that makes checking quick

    The seed is fixed so the sample is reproducible - if anyone asks how you
    chose these products, the answer is "randomly, with seed 42", and they can
    regenerate the identical list.
    """
    rows = Storage().read_all()
    if not rows:
        log.error("No data. Run `python main.py collect` first.")
        return

    if not 5 <= percent <= 10:
        log.warning("Criterion 7.1 asks for 5-10%%. You chose %s%%.", percent)

    count = max(1, round(len(rows) * percent / 100))
    random.Random(seed).shuffle(rows)
    sample = rows[:count]

    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ---------------- the evidence table ----------------
    with open(SAMPLE_CSV, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, COLUMNS)
        writer.writeheader()
        for row in sample:
            record = {"asin": row.get("asin"), "product_url": row.get("product_url")}
            for field in CHECK_FIELDS:
                record[f"scraped_{field}"] = row.get(field)
                record[f"matches_{field}"] = ""       # you fill this in
            record["image_opens"] = ""
            record["video_link_works"] = "n/a" if not row.get("has_video") else ""
            record["notes"] = ""
            writer.writerow(record)

    _write_sheet(sample, percent, len(rows))

    log.info("Sample: %s of %s products (%s%%)", count, len(rows), percent)
    log.info("  Checklist : %s", SAMPLE_CSV)
    log.info("  Open this : %s", SHEET_HTML)
    log.info("Open the HTML sheet, tick as you go, then click Export.")


def _write_sheet(sample, percent, total):
    """Write a local HTML page that makes the manual check fast."""
    cards = []
    for index, row in enumerate(sample, start=1):
        asin = html_escape.escape(str(row.get("asin") or ""))
        url = html_escape.escape(str(row.get("product_url") or ""))
        image = html_escape.escape(str(row.get("image_url") or ""))
        video_page = html_escape.escape(str(row.get("video_page_url") or ""))
        has_video = bool(row.get("has_video"))

        field_rows = ""
        for field in CHECK_FIELDS:
            value = row.get(field)
            shown = html_escape.escape("(empty)" if value in (None, "") else str(value))
            empty_class = " empty" if value in (None, "") else ""
            field_rows += f"""
            <tr>
              <th>{field}</th>
              <td class="val{empty_class}">{shown}</td>
              <td class="tick">
                <label><input type="radio" name="{asin}_{field}" value="yes"> match</label>
                <label><input type="radio" name="{asin}_{field}" value="no"> differs</label>
              </td>
            </tr>"""

        video_block = (
            f'<a href="{video_page}" target="_blank">open video page</a>'
            f'<label><input type="radio" name="{asin}_video" value="yes"> plays</label>'
            f'<label><input type="radio" name="{asin}_video" value="no"> broken</label>'
            if has_video else '<span class="na">no video on this product</span>'
        )

        cards.append(f"""
        <article class="card" data-asin="{asin}">
          <header>
            <span class="num">{index}</span>
            <span class="asin">{asin}</span>
            <a class="open" href="{url}" target="_blank">open product page &rarr;</a>
          </header>
          <table>{field_rows}</table>
          <div class="media">
            <div>
              <a href="{image}" target="_blank">open image</a>
              <label><input type="radio" name="{asin}_image" value="yes"> shows picture</label>
              <label><input type="radio" name="{asin}_image" value="no"> broken</label>
            </div>
            <div>{video_block}</div>
          </div>
          <input class="notes" data-asin="{asin}" placeholder="notes (optional)">
        </article>""")

    page = _SHEET_TEMPLATE.replace("{{CARDS}}", "\n".join(cards))
    page = page.replace("{{COUNT}}", str(len(sample)))
    page = page.replace("{{TOTAL}}", str(total))
    page = page.replace("{{PERCENT}}", str(percent))
    page = page.replace("{{FIELDS}}", json.dumps(CHECK_FIELDS))
    SHEET_HTML.write_text(page, encoding="utf-8")


def summary():
    """
    Read back your filled-in results and print the pass rate.

    Accepts either the exported file from the HTML sheet, or the CSV filled in
    by hand. Both use the same column names.
    """
    path = RESULT_CSV if RESULT_CSV.exists() else SAMPLE_CSV
    if not path.exists():
        log.error("No results found. Run `python main.py spotcheck` first.")
        return

    with open(path, encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    if not rows:
        log.error("%s is empty.", path.name)
        return

    print("\n" + "=" * 62)
    print("  SPOT-CHECK RESULTS  (acceptance criterion 7.1)")
    print("=" * 62)
    print(f"  Source   : {path.name}")
    print(f"  Products : {len(rows)}\n")

    def tally(column):
        yes = sum(1 for r in rows if str(r.get(column, "")).strip().lower() in ("yes", "y", "1", "true"))
        no = sum(1 for r in rows if str(r.get(column, "")).strip().lower() in ("no", "n", "0", "false"))
        return yes, no, len(rows) - yes - no

    print(f"  {'field':<18}{'match':>7}{'differs':>9}{'blank':>7}   rate")
    overall_yes = overall_checked = 0
    for field in CHECK_FIELDS:
        yes, no, blank = tally(f"matches_{field}")
        checked = yes + no
        rate = f"{yes / checked * 100:5.1f}%" if checked else "    -"
        print(f"  {field:<18}{yes:>7}{no:>9}{blank:>7}   {rate}")
        overall_yes += yes
        overall_checked += checked

    for label, column in (("image opens", "image_opens"),
                          ("video link works", "video_link_works")):
        yes, no, blank = tally(column)
        na = sum(1 for r in rows if str(r.get(column, "")).strip().lower() == "n/a")
        checked = yes + no
        rate = f"{yes / checked * 100:5.1f}%" if checked else "    -"
        print(f"  {label:<18}{yes:>7}{no:>9}{blank - na:>7}   {rate}"
              + (f"   ({na} n/a)" if na else ""))

    print("\n" + "-" * 62)
    if overall_checked:
        print(f"  OVERALL FIELD ACCURACY : {overall_yes}/{overall_checked} "
              f"= {overall_yes / overall_checked * 100:.1f}%")
    else:
        print("  Nothing ticked yet - open the HTML sheet and check the sample.")

    problems = [r for r in rows if any(
        str(r.get(f"matches_{f}", "")).strip().lower() in ("no", "n")
        for f in CHECK_FIELDS)]
    if problems:
        print(f"\n  {len(problems)} product(s) with a mismatch - investigate:")
        for row in problems[:10]:
            bad = [f for f in CHECK_FIELDS
                   if str(row.get(f"matches_{f}", "")).strip().lower() in ("no", "n")]
            print(f"    {row['asin']}  ->  {', '.join(bad)}")
        print("\n  A field wrong across several products means a broken")
        print("  selector - fix it in scraper/selectors.py.")
    print("=" * 62 + "\n")


_SHEET_TEMPLATE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Spot check - acceptance criterion 7.1</title>
<style>
  :root{--ink:#16202E;--soft:#4A5A6E;--line:#D3DBE3;--blue:#2E5C8A;
        --ok:#0F7F6C;--warn:#B8620B;}
  *{box-sizing:border-box}
  body{margin:0;background:#EEF1F4;color:var(--ink);
       font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;padding:2rem 1rem 6rem}
  .wrap{max-width:900px;margin:0 auto}
  h1{font-size:1.5rem;margin:0 0 .35rem}
  .lead{color:var(--soft);margin:0 0 1.5rem;font-size:.93rem}
  .how{background:#fff;border:1px solid var(--line);border-left:3px solid var(--blue);
       padding:1rem 1.25rem;margin-bottom:2rem;font-size:.9rem}
  .how ol{margin:.5rem 0 0;padding-left:1.2rem}
  .how li{margin-bottom:.3rem}
  .card{background:#fff;border:1px solid var(--line);border-radius:4px;
        padding:1rem 1.25rem;margin-bottom:1rem}
  .card header{display:flex;align-items:center;gap:.75rem;
               border-bottom:1px solid var(--line);padding-bottom:.6rem;margin-bottom:.6rem}
  .num{background:var(--blue);color:#fff;width:1.6rem;height:1.6rem;border-radius:50%;
       display:grid;place-items:center;font-size:.75rem;font-weight:600}
  .asin{font-family:ui-monospace,Menlo,monospace;font-size:.85rem;color:var(--soft)}
  .open{margin-left:auto;color:var(--blue);font-weight:600;text-decoration:none;font-size:.9rem}
  .open:hover{text-decoration:underline}
  table{width:100%;border-collapse:collapse}
  th{text-align:left;font-weight:600;color:var(--soft);font-size:.82rem;
     width:7.5rem;padding:.3rem 0;vertical-align:top}
  td{padding:.3rem 0;vertical-align:top}
  .val{font-size:.9rem;word-break:break-word;padding-right:1rem}
  .val.empty{color:var(--warn);font-style:italic}
  .tick{width:11rem;white-space:nowrap}
  .tick label{font-size:.8rem;color:var(--soft);margin-right:.6rem;cursor:pointer}
  .media{display:flex;gap:2rem;flex-wrap:wrap;margin-top:.7rem;padding-top:.7rem;
         border-top:1px solid var(--line);font-size:.85rem}
  .media a{color:var(--blue);margin-right:.6rem}
  .media label{color:var(--soft);margin-right:.5rem;cursor:pointer}
  .na{color:var(--soft);font-style:italic}
  .notes{width:100%;margin-top:.6rem;padding:.4rem .6rem;border:1px solid var(--line);
         border-radius:3px;font:inherit;font-size:.85rem}
  .bar{position:fixed;left:0;right:0;bottom:0;background:#fff;border-top:1px solid var(--line);
       padding:.8rem 1rem;display:flex;gap:1rem;align-items:center;justify-content:center}
  .bar button{background:var(--ok);color:#fff;border:0;padding:.6rem 1.4rem;
              border-radius:4px;font:inherit;font-weight:600;cursor:pointer}
  .bar button:hover{filter:brightness(1.08)}
  .prog{color:var(--soft);font-size:.9rem}
</style></head><body>
<div class="wrap">
  <h1>Spot check &mdash; {{COUNT}} of {{TOTAL}} products ({{PERCENT}}%)</h1>
  <p class="lead">Acceptance criterion 7.1. Sample chosen randomly with a fixed seed, so it is reproducible.</p>

  <div class="how"><strong>How to do this</strong>
    <ol>
      <li>Click <em>open product page</em> &mdash; it opens in a new tab.</li>
      <li>Compare each value with what the live page shows. Tick match or differs.</li>
      <li>Click <em>open image</em> &mdash; it should show only the picture.</li>
      <li>If the product has a video, open the video page and confirm it plays.</li>
      <li>When finished, press <strong>Export results</strong> at the bottom.</li>
    </ol>
    Small formatting differences are fine (e.g. <em>$79.99</em> vs <em>79.99</em>).
    Tick <em>differs</em> only when the value itself is wrong.
  </div>

  {{CARDS}}
</div>

<div class="bar">
  <span class="prog" id="prog">0 answered</span>
  <button onclick="exportCsv()">Export results</button>
</div>

<script>
const FIELDS = {{FIELDS}};

function answered(){
  return document.querySelectorAll('input[type=radio]:checked').length;
}
function refresh(){
  const total = document.querySelectorAll('.card').length * (FIELDS.length + 2);
  document.getElementById('prog').textContent = answered() + ' of ~' + total + ' answered';
}
document.addEventListener('change', refresh);
refresh();

function pick(name){
  const el = document.querySelector('input[name="' + name + '"]:checked');
  return el ? el.value : '';
}

function exportCsv(){
  const head = ['asin','product_url']
    .concat(FIELDS.map(f => 'scraped_' + f))
    .concat(FIELDS.map(f => 'matches_' + f))
    .concat(['image_opens','video_link_works','notes']);

  const lines = [head.join(',')];

  document.querySelectorAll('.card').forEach(card => {
    const asin = card.dataset.asin;
    const url = card.querySelector('.open').getAttribute('href');
    const vals = Array.from(card.querySelectorAll('.val')).map(td => td.textContent.trim());
    const notes = card.querySelector('.notes').value;
    const videoEl = card.querySelector('input[name="' + asin + '_video"]');

    const row = [asin, url]
      .concat(vals)
      .concat(FIELDS.map(f => pick(asin + '_' + f)))
      .concat([pick(asin + '_image'), videoEl ? pick(asin + '_video') : 'n/a', notes]);

    lines.push(row.map(v => '"' + String(v).replace(/"/g,'""') + '"').join(','));
  });

  const blob = new Blob([lines.join('\\n')], {type:'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'spotcheck_results.csv';
  a.click();
  alert('Saved as spotcheck_results.csv.\\n\\nMove it into your data/ folder, then run:\\n  python main.py spotcheck --summary');
}
</script>
</body></html>
"""
