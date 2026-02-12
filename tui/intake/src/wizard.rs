use crate::project::*;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Step {
    Name,
    Physical,
    Color,
    Typography,
    Brief,
    Domains,
    References,
    Output,
    Review,
}

impl Step {
    pub const ALL: &[Step] = &[
        Step::Name,
        Step::Physical,
        Step::Color,
        Step::Typography,
        Step::Brief,
        Step::Domains,
        Step::References,
        Step::Output,
        Step::Review,
    ];

    pub fn index(&self) -> usize {
        Step::ALL.iter().position(|s| s == self).unwrap()
    }

    pub fn title(&self) -> &str {
        match self {
            Step::Name => "Project Name & Type",
            Step::Physical => "Physical Specs",
            Step::Color => "Color System",
            Step::Typography => "Typography",
            Step::Brief => "Creative Brief",
            Step::Domains => "Domain Selection",
            Step::References => "Reference Materials",
            Step::Output => "Output Requirements",
            Step::Review => "Review & Confirm",
        }
    }

    pub fn next(&self) -> Option<Step> {
        let i = self.index();
        Step::ALL.get(i + 1).copied()
    }

    pub fn prev(&self) -> Option<Step> {
        let i = self.index();
        if i > 0 {
            Step::ALL.get(i - 1).copied()
        } else {
            None
        }
    }
}

pub struct Wizard {
    pub step: Step,
    pub spec: ProjectSpec,
    pub input_buf: String,
    pub field_index: usize,
    pub type_index: usize,
    pub sides_index: usize,
    pub color_mode_index: usize,
    pub domain_toggles: Vec<bool>,
    #[allow(dead_code)]
    pub confirmed: bool,
    pub scaffolded: bool,
}

const AVAILABLE_DOMAINS: &[&str] = &[
    "design",
    "dataviz",
    "editorial",
    "creative-production",
    "agency-mix",
];

impl Wizard {
    pub fn new() -> Self {
        Self {
            step: Step::Name,
            spec: ProjectSpec::default(),
            input_buf: String::new(),
            field_index: 0,
            type_index: 0,
            sides_index: 1,
            color_mode_index: 1,
            domain_toggles: vec![true, false, false, true, false],
            confirmed: false,
            scaffolded: false,
        }
    }

    pub fn advance(&mut self) {
        self.commit_current();
        if let Some(next) = self.step.next() {
            self.step = next;
            self.field_index = 0;
            self.load_step_buf();
        }
    }

    pub fn go_back(&mut self) {
        self.commit_current();
        if let Some(prev) = self.step.prev() {
            self.step = prev;
            self.field_index = 0;
            self.load_step_buf();
        }
    }

    pub fn load_step_buf(&mut self) {
        self.input_buf = match self.step {
            Step::Name => self.spec.name.clone(),
            Step::Brief => self.spec.brief.clone(),
            Step::Typography => match self.field_index {
                0 => self.spec.typography.primary_font.clone(),
                1 => self.spec.typography.secondary_font.clone(),
                _ => self.spec.typography.notes.clone(),
            },
            Step::References => self.spec.references.join("\n"),
            Step::Output => self.spec.output.delivery_notes.clone(),
            _ => String::new(),
        };
    }

    pub fn commit_current(&mut self) {
        match self.step {
            Step::Name => {
                self.spec.name = self.input_buf.trim().to_string();
                self.spec.project_type = ProjectType::from_index(self.type_index, &self.input_buf);
            }
            Step::Physical => {
                self.spec.physical.sides = if self.sides_index == 0 {
                    Sides::Single
                } else {
                    Sides::Double
                };
            }
            Step::Color => {
                self.spec.color.mode = match self.color_mode_index {
                    0 => ColorMode::OneColor,
                    1 => ColorMode::TwoColor,
                    _ => ColorMode::FullProcess,
                };
            }
            Step::Typography => match self.field_index {
                0 => self.spec.typography.primary_font = self.input_buf.trim().to_string(),
                1 => self.spec.typography.secondary_font = self.input_buf.trim().to_string(),
                _ => self.spec.typography.notes = self.input_buf.trim().to_string(),
            },
            Step::Brief => {
                self.spec.brief = self.input_buf.trim().to_string();
            }
            Step::Domains => {
                self.spec.domains = AVAILABLE_DOMAINS
                    .iter()
                    .zip(self.domain_toggles.iter())
                    .filter(|(_, &on)| on)
                    .map(|(d, _)| d.to_string())
                    .collect();
            }
            Step::References => {
                self.spec.references = self
                    .input_buf
                    .lines()
                    .map(|l| l.trim().to_string())
                    .filter(|l| !l.is_empty())
                    .collect();
            }
            Step::Output => {
                self.spec.output.delivery_notes = self.input_buf.trim().to_string();
            }
            Step::Review => {}
        }
    }

    pub fn toggle_domain(&mut self) {
        if self.field_index < self.domain_toggles.len() {
            self.domain_toggles[self.field_index] = !self.domain_toggles[self.field_index];
        }
    }

    pub fn available_domains(&self) -> &[&str] {
        AVAILABLE_DOMAINS
    }

    pub fn field_count(&self) -> usize {
        match self.step {
            Step::Name => 2,
            Step::Physical => 4,
            Step::Color => 3,
            Step::Typography => 3,
            Step::Domains => AVAILABLE_DOMAINS.len(),
            _ => 1,
        }
    }

    pub fn next_field(&mut self) {
        self.commit_current();
        if self.field_index + 1 < self.field_count() {
            self.field_index += 1;
            self.load_step_buf();
        }
    }

    pub fn prev_field(&mut self) {
        self.commit_current();
        if self.field_index > 0 {
            self.field_index -= 1;
            self.load_step_buf();
        }
    }

    pub fn scaffold(&self) -> std::io::Result<String> {
        let dir = self.spec.scaffold_dir();
        std::fs::create_dir_all(format!("{dir}/cards/front"))?;
        std::fs::create_dir_all(format!("{dir}/cards/back"))?;
        std::fs::create_dir_all(format!("{dir}/reference"))?;
        std::fs::create_dir_all(format!("{dir}/moodboard"))?;
        std::fs::create_dir_all(format!("{dir}/output"))?;

        let yaml = self.spec.to_yaml().map_err(|e| {
            std::io::Error::new(std::io::ErrorKind::Other, e.to_string())
        })?;
        std::fs::write(format!("{dir}/project.yaml"), &yaml)?;

        let brief = self.spec.brief_md();
        std::fs::write(format!("{dir}/brief.md"), &brief)?;

        std::fs::write(format!("{dir}/decisions.json"), "[]")?;
        std::fs::write(format!("{dir}/design-notes.md"), &format!("# Design Notes: {}\n", self.spec.name))?;

        Ok(dir)
    }
}
