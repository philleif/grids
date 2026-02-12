use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// A node in the decision tree tracking how a design choice was made.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Decision {
    pub id: String,
    pub parent_id: Option<String>,
    pub timestamp: String,
    pub agent: String,
    pub kind: DecisionKind,
    pub rationale: String,
    pub influences: Vec<Influence>,
    pub alternatives_considered: Vec<Alternative>,
    pub confidence: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum DecisionKind {
    Layout { property: String, value: String },
    Typography { property: String, value: String },
    Color { property: String, value: String },
    Content { property: String, value: String },
    Composition { description: String },
    StyleDirection { description: String },
    Revision { original_decision_id: String, reason: String },
}

/// A reference that influenced a decision.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Influence {
    pub source: InfluenceSource,
    pub relevance: String,
    pub weight: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type")]
pub enum InfluenceSource {
    Book { title: String, chunk_id: String, excerpt: String },
    Moodboard { board_id: String, ref_id: String, description: String },
    AgentKnowledge { agent: String, skill: String, note: String },
    UserDirection { input: String },
    PriorDecision { decision_id: String },
}

/// An alternative that was considered but not chosen.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Alternative {
    pub description: String,
    pub reason_rejected: String,
}

/// The full decision tree for a project.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DecisionTree {
    pub project_id: String,
    pub decisions: Vec<Decision>,
    #[serde(skip)]
    index: HashMap<String, usize>,
}

impl DecisionTree {
    pub fn new(project_id: &str) -> Self {
        Self {
            project_id: project_id.to_string(),
            decisions: Vec::new(),
            index: HashMap::new(),
        }
    }

    pub fn add(&mut self, decision: Decision) {
        let idx = self.decisions.len();
        self.index.insert(decision.id.clone(), idx);
        self.decisions.push(decision);
    }

    pub fn get(&self, id: &str) -> Option<&Decision> {
        self.index.get(id).map(|&i| &self.decisions[i])
    }

    /// Walk ancestors from a decision back to the root(s).
    pub fn lineage(&self, id: &str) -> Vec<&Decision> {
        let mut chain = Vec::new();
        let mut current = id;
        while let Some(d) = self.get(current) {
            chain.push(d);
            match &d.parent_id {
                Some(pid) => current = pid,
                None => break,
            }
        }
        chain
    }

    /// All decisions that cite a specific influence source.
    pub fn decisions_influenced_by_book(&self, title: &str) -> Vec<&Decision> {
        self.decisions
            .iter()
            .filter(|d| {
                d.influences.iter().any(|inf| matches!(
                    &inf.source,
                    InfluenceSource::Book { title: t, .. } if t == title
                ))
            })
            .collect()
    }

    /// All decisions that cite a specific moodboard reference.
    pub fn decisions_influenced_by_ref(&self, ref_id: &str) -> Vec<&Decision> {
        self.decisions
            .iter()
            .filter(|d| {
                d.influences.iter().any(|inf| matches!(
                    &inf.source,
                    InfluenceSource::Moodboard { ref_id: r, .. } if r == ref_id
                ))
            })
            .collect()
    }

    /// Generate a markdown design notes document.
    pub fn to_design_notes(&self) -> String {
        let mut md = format!("# Design Notes: {}\n\n", self.project_id);

        for d in &self.decisions {
            md.push_str(&format!("## {}\n\n", d.id));
            md.push_str(&format!("**Agent:** {} | **Confidence:** {:.0}%\n\n", d.agent, d.confidence * 100.0));
            md.push_str(&format!("**Rationale:** {}\n\n", d.rationale));

            if !d.influences.is_empty() {
                md.push_str("**Influences:**\n");
                for inf in &d.influences {
                    let src = match &inf.source {
                        InfluenceSource::Book { title, excerpt, .. } => {
                            format!("Book: *{}* -- \"{}\"", title, truncate(excerpt, 80))
                        }
                        InfluenceSource::Moodboard { description, .. } => {
                            format!("Moodboard: {}", description)
                        }
                        InfluenceSource::AgentKnowledge { agent, note, .. } => {
                            format!("Agent {}: {}", agent, note)
                        }
                        InfluenceSource::UserDirection { input } => {
                            format!("User: {}", input)
                        }
                        InfluenceSource::PriorDecision { decision_id } => {
                            format!("Prior decision: {}", decision_id)
                        }
                    };
                    md.push_str(&format!("- {} (weight: {:.1})\n", src, inf.weight));
                }
                md.push('\n');
            }

            if !d.alternatives_considered.is_empty() {
                md.push_str("**Alternatives considered:**\n");
                for alt in &d.alternatives_considered {
                    md.push_str(&format!("- ~~{}~~ -- {}\n", alt.description, alt.reason_rejected));
                }
                md.push('\n');
            }

            md.push_str("---\n\n");
        }

        md
    }

    pub fn rebuild_index(&mut self) {
        self.index.clear();
        for (i, d) in self.decisions.iter().enumerate() {
            self.index.insert(d.id.clone(), i);
        }
    }
}

fn truncate(s: &str, max: usize) -> &str {
    if s.len() <= max {
        s
    } else {
        &s[..max]
    }
}
