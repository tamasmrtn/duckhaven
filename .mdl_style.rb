all

# Match mdl's built-in "default" style, then adjust two rules:
exclude_rule "fenced-code-language" # MD040
exclude_rule "first-line-h1"        # MD041

# 120-char lines; tables are wide reference grids that can't be wrapped.
rule "MD013", line_length: 120, tables: false
rule "MD029", style: :ordered
