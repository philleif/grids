use serde::{Deserialize, Serialize};

use crate::grid::Grid;

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub enum PageSize {
    A4,
    A5,
    Letter,
    HalfLetter,
    Custom { width: f64, height: f64 },
}

impl PageSize {
    /// Returns (width, height) in points (1pt = 1/72 inch).
    pub fn dimensions(&self) -> (f64, f64) {
        match self {
            PageSize::A4 => (595.28, 841.89),
            PageSize::A5 => (419.53, 595.28),
            PageSize::Letter => (612.0, 792.0),
            PageSize::HalfLetter => (396.0, 612.0),
            PageSize::Custom { width, height } => (*width, *height),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Page {
    pub number: u32,
    pub size: PageSize,
    pub grid: Grid,
    pub blocks: Vec<Block>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Block {
    pub id: String,
    pub col: u32,
    pub row: u32,
    pub col_span: u32,
    pub row_span: u32,
    pub content: BlockContent,
    /// Links this block to a decision in the provenance tree.
    pub decision_ids: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum BlockContent {
    Text { body: String, style: TextStyle },
    Image { path: String, alt: String },
    Empty,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TextStyle {
    pub font_size: f64,
    pub font_family: String,
    pub line_height: f64,
    pub weight: String,
}

impl Default for TextStyle {
    fn default() -> Self {
        Self {
            font_size: 10.0,
            font_family: "Helvetica".to_string(),
            line_height: 1.4,
            weight: "normal".to_string(),
        }
    }
}

impl Page {
    pub fn new(number: u32, size: PageSize, columns: u32, rows: u32) -> Self {
        let (w, h) = size.dimensions();
        Self {
            number,
            size,
            grid: Grid::new(columns, rows, w, h),
            blocks: Vec::new(),
        }
    }

    pub fn add_block(&mut self, block: Block) {
        self.blocks.push(block);
    }
}
