#!/usr/bin/env python3
"""Regenerate the two-column variant from the single-column source."""
import re
s = open('Team6_Phase1.tex').read()

s = s.replace("\\documentclass[11pt,a4paper]{article}", "\\documentclass[10pt,a4paper,twocolumn]{article}")
s = s.replace("\\usepackage[margin=1in]{geometry}",
              "\\usepackage[margin=0.85in]{geometry}\n\\setlength{\\columnsep}{18pt}")
# keep floats close to the text that cites them
s = s.replace("\\setlist[itemize]{itemsep=2pt,topsep=4pt}",
              "\\setlist[itemize]{itemsep=2pt,topsep=4pt}\n"
              "\\renewcommand{\\topfraction}{0.9}\n\\renewcommand{\\textfraction}{0.07}\n"
              "\\renewcommand{\\floatpagefraction}{0.75}\n\\setcounter{topnumber}{2}")
s = s.replace("\\clearpage\n\\tableofcontents\n\\clearpage\n\\listoftables\n\\clearpage",
              "\\onecolumn\n\\tableofcontents\n\\clearpage\n\\listoftables\n\\clearpage\n\\twocolumn")

def conv(m):
    body = m.group(0)
    spec = re.search(r"\\begin\{longtable\}\{(.*?)\}\n", body).group(1)
    inner = body.split("\n", 1)[1].rsplit("\\end{longtable}", 1)[0]
    cap = re.search(r"\\caption\{(.*?)\}\\label\{(.*?)\}\\\\\n", inner, re.S)
    caption, label = cap.group(1), cap.group(2)
    inner = inner[cap.end():].replace("\\endfirsthead", "").replace("\\endhead", "")
    parts = inner.split("\\midrule")
    if len(parts) >= 3:                      # drop longtable's repeated header
        inner = parts[0] + "\\midrule" + "\\midrule".join(parts[2:])
    for a, b in [("0.155\\textwidth", "0.16\\linewidth"), ("0.235\\textwidth", "0.24\\linewidth"),
                 ("0.20\\textwidth", "0.21\\linewidth"), ("0.28\\textwidth", "0.29\\linewidth"),
                 ("0.29\\textwidth", "0.30\\linewidth"), ("0.32\\textwidth", "0.33\\linewidth"),
                 ("0.26\\textwidth", "0.27\\linewidth"), ("0.11\\textwidth", "0.12\\linewidth"),
                 ("0.70\\textwidth", "0.72\\linewidth"), ("0.37\\textwidth", "0.38\\linewidth"),
                 ("0.40\\textwidth", "0.41\\linewidth")]:
        spec = spec.replace(a, b)
    return ("\\begin{table*}[tbp]\n\\centering\\footnotesize\n"
            "\\caption{%s}\\label{%s}\n\\begin{tabular}{%s}\n%s\n\\end{tabular}\n\\end{table*}"
            % (caption, label, spec, inner.strip()))

s = re.sub(r"\{\\footnotesize\n\\begin\{longtable\}.*?\\end\{longtable\}\n\}", conv, s, flags=re.S)
s = s.replace("basicstyle=\\ttfamily\\footnotesize,", "basicstyle=\\ttfamily\\scriptsize,")
s = s.replace("\\begin{figure}[htbp]", "\\begin{figure*}[tbp]").replace("\\end{figure}", "\\end{figure*}")
s = s.replace("\\begin{tikzpicture}[\n  font=\\scriptsize,", "\\begin{tikzpicture}[\n  font=\\scriptsize,")
open('Team6_Phase1_twocol.tex', 'w').write(s)
print("two-column variant regenerated")
