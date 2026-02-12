use crate::page::{Block, BlockContent, Page};

/// Render a page to SVG string.
pub fn page_to_svg(page: &Page) -> String {
    let (pw, ph) = page.size.dimensions();
    let mut svg = format!(
        "<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 {pw} {ph}\" width=\"{pw}\" height=\"{ph}\">"
    );

    svg.push_str(&format!(
        "<rect width=\"{pw}\" height=\"{ph}\" fill=\"white\"/>"
    ));

    let guide_stroke = "#e0e0e0";
    for col in 0..page.grid.columns {
        for row in 0..page.grid.rows {
            let (x, y) = page.grid.cell_origin(col, row);
            let (w, h) = page.grid.span_size(1, 1);
            svg.push_str(&format!(
                "<rect x=\"{x}\" y=\"{y}\" width=\"{w}\" height=\"{h}\" fill=\"none\" stroke=\"{guide_stroke}\" stroke-width=\"0.25\"/>"
            ));
        }
    }

    for block in &page.blocks {
        render_block(&mut svg, &page.grid, block);
    }

    svg.push_str("</svg>");
    svg
}

fn render_block(svg: &mut String, grid: &crate::grid::Grid, block: &Block) {
    let (x, y) = grid.cell_origin(block.col, block.row);
    let (w, h) = grid.span_size(block.col_span, block.row_span);

    match &block.content {
        BlockContent::Text { body, style } => {
            let stroke = "#333";
            let fill = "#111";
            svg.push_str(&format!(
                "<rect x=\"{x}\" y=\"{y}\" width=\"{w}\" height=\"{h}\" fill=\"none\" stroke=\"{stroke}\" stroke-width=\"0.5\"/>"
            ));
            let text_x = x + 4.0;
            let text_y = y + style.font_size + 4.0;
            svg.push_str(&format!(
                "<text x=\"{text_x}\" y=\"{text_y}\" font-family=\"{}\" font-size=\"{}\" fill=\"{fill}\">",
                style.font_family, style.font_size
            ));
            svg.push_str(&xml_escape(body));
            svg.push_str("</text>");
        }
        BlockContent::Image { path, alt } => {
            let bg = "#f0f0f0";
            let stroke = "#999";
            let text_fill = "#999";
            svg.push_str(&format!(
                "<rect x=\"{x}\" y=\"{y}\" width=\"{w}\" height=\"{h}\" fill=\"{bg}\" stroke=\"{stroke}\" stroke-width=\"0.5\"/>"
            ));
            let label = if alt.is_empty() { path.as_str() } else { alt.as_str() };
            let cx = x + w / 2.0;
            let cy = y + h / 2.0;
            svg.push_str(&format!(
                "<text x=\"{cx}\" y=\"{cy}\" text-anchor=\"middle\" font-size=\"8\" fill=\"{text_fill}\">[{label}]</text>"
            ));
        }
        BlockContent::Empty => {
            let stroke = "#ccc";
            svg.push_str(&format!(
                "<rect x=\"{x}\" y=\"{y}\" width=\"{w}\" height=\"{h}\" fill=\"none\" stroke=\"{stroke}\" stroke-width=\"0.25\" stroke-dasharray=\"4,2\"/>"
            ));
        }
    }
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

/// Render a page to LaTeX string (standalone document).
pub fn page_to_latex(page: &Page) -> String {
    let (pw, ph) = page.size.dimensions();
    let pw_cm = pw / 72.0 * 2.54;
    let ph_cm = ph / 72.0 * 2.54;
    let m = &page.grid.margin;

    let mut tex = String::new();
    tex.push_str("\\documentclass{article}\n");
    tex.push_str("\\usepackage[utf8]{inputenc}\n");
    tex.push_str(&format!(
        "\\usepackage[paperwidth={pw_cm:.2}cm,paperheight={ph_cm:.2}cm,top={top:.2}pt,bottom={bot:.2}pt,left={left:.2}pt,right={right:.2}pt]{{geometry}}\n",
        top = m.top, bot = m.bottom, left = m.left, right = m.right
    ));
    tex.push_str("\\usepackage{tikz}\n");
    tex.push_str("\\pagestyle{empty}\n");
    tex.push_str("\\begin{document}\n");
    tex.push_str("\\noindent\n");
    tex.push_str("\\begin{tikzpicture}[x=1pt,y=-1pt]\n");

    for block in &page.blocks {
        let (x, y) = page.grid.cell_origin(block.col, block.row);
        let (w, h) = page.grid.span_size(block.col_span, block.row_span);
        let bx = x - m.left;
        let by = y - m.top;

        match &block.content {
            BlockContent::Text { body, style } => {
                let fs = style.font_size;
                let escaped = latex_escape(body);
                tex.push_str(&format!(
                    "\\node[anchor=north west,text width={w:.1}pt,font=\\fontsize{{{fs:.1}}}{{\\baselineskip}}\\selectfont] at ({bx:.1},{by:.1}) {{{escaped}}};\n"
                ));
            }
            BlockContent::Image { path, .. } => {
                tex.push_str(&format!(
                    "\\node[anchor=north west,inner sep=0] at ({bx:.1},{by:.1}) {{\\includegraphics[width={w:.1}pt,height={h:.1}pt]{{{path}}}}};\n"
                ));
            }
            BlockContent::Empty => {}
        }
    }

    tex.push_str("\\end{tikzpicture}\n");
    tex.push_str("\\end{document}\n");
    tex
}

fn latex_escape(s: &str) -> String {
    s.replace('\\', "\\textbackslash{}")
        .replace('{', "\\{")
        .replace('}', "\\}")
        .replace('&', "\\&")
        .replace('%', "\\%")
        .replace('$', "\\$")
        .replace('#', "\\#")
        .replace('_', "\\_")
        .replace('~', "\\textasciitilde{}")
        .replace('^', "\\textasciicircum{}")
}
