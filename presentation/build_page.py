"""Embed results.json into page_template.html -> query-comparison.html."""
import json, pathlib

here = pathlib.Path(__file__).parent
data = json.loads((here / "results.json").read_text())
page = (here / "page_template.html").read_text()
out = page.replace("/*__RESULTS__*/null", json.dumps(data).replace("</", "<\\/"))
(here / "query-comparison.html").write_text(out)
print(f"wrote {here / 'query-comparison.html'}")
