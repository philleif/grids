use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectSpec {
    pub name: String,
    pub project_type: ProjectType,
    pub physical: PhysicalSpec,
    pub color: ColorSpec,
    pub typography: TypographySpec,
    pub brief: String,
    pub domains: Vec<String>,
    pub references: Vec<String>,
    pub output: OutputSpec,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ProjectType {
    CallingCards,
    Zine,
    Poster,
    Editorial,
    Other(String),
}

impl ProjectType {
    pub const VARIANTS: &[&str] = &["calling-cards", "zine", "poster", "editorial", "other"];

    pub fn from_index(i: usize, custom: &str) -> Self {
        match i {
            0 => Self::CallingCards,
            1 => Self::Zine,
            2 => Self::Poster,
            3 => Self::Editorial,
            _ => Self::Other(custom.to_string()),
        }
    }

    pub fn label(&self) -> &str {
        match self {
            Self::CallingCards => "calling-cards",
            Self::Zine => "zine",
            Self::Poster => "poster",
            Self::Editorial => "editorial",
            Self::Other(s) => s.as_str(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PhysicalSpec {
    pub item_width_inches: f64,
    pub item_height_inches: f64,
    pub stock_width_inches: f64,
    pub stock_height_inches: f64,
    pub sides: Sides,
    pub bleed_inches: f64,
    pub quantity: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Sides {
    Single,
    Double,
}

impl Sides {
    pub fn label(&self) -> &str {
        match self {
            Self::Single => "single-sided",
            Self::Double => "double-sided",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ColorSpec {
    pub mode: ColorMode,
    pub primary: CmykColor,
    pub secondary: Option<CmykColor>,
    pub spot_colors: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ColorMode {
    OneColor,
    TwoColor,
    FullProcess,
}

impl ColorMode {
    pub fn label(&self) -> &str {
        match self {
            Self::OneColor => "1-color",
            Self::TwoColor => "2-color",
            Self::FullProcess => "full-process (CMYK)",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CmykColor {
    pub c: f64,
    pub m: f64,
    pub y: f64,
    pub k: f64,
    pub name: String,
}

impl std::fmt::Display for CmykColor {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "{} (C:{:.0} M:{:.0} Y:{:.0} K:{:.0})",
            self.name, self.c, self.m, self.y, self.k
        )
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TypographySpec {
    pub primary_font: String,
    pub secondary_font: String,
    pub notes: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OutputSpec {
    pub formats: Vec<String>,
    pub impose: bool,
    pub delivery_notes: String,
}

impl Default for ProjectSpec {
    fn default() -> Self {
        Self {
            name: String::new(),
            project_type: ProjectType::CallingCards,
            physical: PhysicalSpec {
                item_width_inches: 3.07,
                item_height_inches: 2.61,
                stock_width_inches: 11.0,
                stock_height_inches: 17.0,
                sides: Sides::Double,
                bleed_inches: 0.125,
                quantity: 4,
            },
            color: ColorSpec {
                mode: ColorMode::TwoColor,
                primary: CmykColor {
                    c: 0.0,
                    m: 0.0,
                    y: 0.0,
                    k: 100.0,
                    name: "Black".to_string(),
                },
                secondary: None,
                spot_colors: Vec::new(),
            },
            typography: TypographySpec {
                primary_font: String::new(),
                secondary_font: String::new(),
                notes: String::new(),
            },
            brief: String::new(),
            domains: vec!["design".to_string()],
            references: Vec::new(),
            output: OutputSpec {
                formats: vec!["pdf".to_string(), "svg".to_string(), "idml".to_string()],
                impose: true,
                delivery_notes: String::new(),
            },
        }
    }
}

impl ProjectSpec {
    pub fn scaffold_dir(&self) -> String {
        let date = chrono::Local::now().format("%Y-%m");
        format!("projects/{}-{}", self.name_slug(), date)
    }

    pub fn name_slug(&self) -> String {
        self.name
            .to_lowercase()
            .replace(|c: char| !c.is_alphanumeric() && c != '-', "-")
            .trim_matches('-')
            .to_string()
    }

    pub fn to_yaml(&self) -> Result<String, serde_yaml::Error> {
        serde_yaml::to_string(self)
    }

    pub fn brief_md(&self) -> String {
        format!(
            "# {}\n\n## Creative Brief\n\n{}\n\n## Physical Specs\n\n- Item: {:.2}\" x {:.2}\"\n- Stock: {:.0}\" x {:.0}\"\n- Sides: {}\n- Bleed: {:.3}\"\n- Quantity: {}\n\n## Color\n\n- Mode: {}\n- Primary: {}\n{}\n\n## Typography\n\n- Primary: {}\n- Secondary: {}\n{}\n",
            self.name,
            self.brief,
            self.physical.item_width_inches,
            self.physical.item_height_inches,
            self.physical.stock_width_inches,
            self.physical.stock_height_inches,
            self.physical.sides.label(),
            self.physical.bleed_inches,
            self.physical.quantity,
            self.color.mode.label(),
            self.color.primary,
            self.color.secondary.as_ref().map_or(String::new(), |c| format!("- Secondary: {c}")),
            self.typography.primary_font,
            self.typography.secondary_font,
            if self.typography.notes.is_empty() { String::new() } else { format!("- Notes: {}", self.typography.notes) },
        )
    }
}
