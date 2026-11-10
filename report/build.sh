#!/bin/bash
# Build a report to a stable state: LaTeX -> BibTeX -> LaTeX x3, then check.
set -e
doc="${1:-Team6_Phase1}"
xelatex -interaction=nonstopmode "$doc" > /dev/null
bibtex "$doc" > /dev/null
xelatex -interaction=nonstopmode "$doc" > /dev/null
bibtex "$doc" > /dev/null
xelatex -interaction=nonstopmode "$doc" > /dev/null
xelatex -interaction=nonstopmode "$doc" > "$doc.build.log" 2>&1
errs=$(grep -c '^!' "$doc.build.log" || true)
rerun=$(grep -ci 'Rerun to get' "$doc.build.log" || true)
undef=$(grep -ci 'undefined' "$doc.build.log" || true)
echo "$doc: errors=$errs rerun-needed=$rerun undefined=$undef"
rm -f "$doc".{aux,bbl,blg,toc,lot,lol,out,log} "$doc.build.log"
