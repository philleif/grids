use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Grid {
    pub columns: u32,
    pub rows: u32,
    pub column_width: f64,
    pub row_height: f64,
    pub gutter_h: f64,
    pub gutter_v: f64,
    pub margin: Margin,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Margin {
    pub top: f64,
    pub right: f64,
    pub bottom: f64,
    pub left: f64,
}

impl Default for Margin {
    fn default() -> Self {
        Self {
            top: 36.0,
            right: 36.0,
            bottom: 36.0,
            left: 36.0,
        }
    }
}

impl Grid {
    pub fn new(columns: u32, rows: u32, page_width: f64, page_height: f64) -> Self {
        let margin = Margin::default();
        let usable_w = page_width - margin.left - margin.right;
        let usable_h = page_height - margin.top - margin.bottom;
        let gutter_h = 12.0;
        let gutter_v = 12.0;
        let col_w = (usable_w - gutter_h * (columns as f64 - 1.0)) / columns as f64;
        let row_h = (usable_h - gutter_v * (rows as f64 - 1.0)) / rows as f64;

        Self {
            columns,
            rows,
            column_width: col_w,
            row_height: row_h,
            gutter_h,
            gutter_v,
            margin,
        }
    }

    /// Returns (x, y) of the top-left corner of a cell.
    pub fn cell_origin(&self, col: u32, row: u32) -> (f64, f64) {
        let x = self.margin.left + col as f64 * (self.column_width + self.gutter_h);
        let y = self.margin.top + row as f64 * (self.row_height + self.gutter_v);
        (x, y)
    }

    /// Returns (width, height) for a block spanning multiple cells.
    pub fn span_size(&self, col_span: u32, row_span: u32) -> (f64, f64) {
        let w = col_span as f64 * self.column_width + (col_span as f64 - 1.0) * self.gutter_h;
        let h = row_span as f64 * self.row_height + (row_span as f64 - 1.0) * self.gutter_v;
        (w, h)
    }
}
