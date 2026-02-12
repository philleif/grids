use ratatui::{
    prelude::*,
    widgets::{Block, Borders, List, ListItem, Paragraph, Wrap},
};

use crate::wizard::{Step, Wizard};

pub fn draw(frame: &mut Frame, wizard: &Wizard) {
    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(10),
            Constraint::Length(3),
        ])
        .split(frame.area());

    draw_header(frame, outer[0], wizard);
    draw_step(frame, outer[1], wizard);
    draw_footer(frame, outer[2], wizard);
}

fn draw_header(frame: &mut Frame, area: Rect, wizard: &Wizard) {
    let steps: Vec<Span> = Step::ALL
        .iter()
        .enumerate()
        .flat_map(|(i, s)| {
            let style = if *s == wizard.step {
                Style::default().fg(Color::Black).bg(Color::White).bold()
            } else if i < wizard.step.index() {
                Style::default().fg(Color::Green)
            } else {
                Style::default().fg(Color::DarkGray)
            };
            let sep = if i < Step::ALL.len() - 1 {
                vec![Span::styled(format!(" {} ", s.title()), style), Span::raw(" > ")]
            } else {
                vec![Span::styled(format!(" {} ", s.title()), style)]
            };
            sep
        })
        .collect();

    let line = Line::from(steps);
    let block = Block::default()
        .title(" GRIDS Intake ")
        .borders(Borders::ALL)
        .border_style(Style::default().fg(Color::Cyan));
    let p = Paragraph::new(line).block(block);
    frame.render_widget(p, area);
}

fn draw_footer(frame: &mut Frame, area: Rect, wizard: &Wizard) {
    let help = if wizard.step == Step::Review {
        " Enter: scaffold project | Backspace: go back | q: quit "
    } else if wizard.step == Step::Domains {
        " Space: toggle | Tab: next field | Enter: next step | Backspace: back | q: quit "
    } else {
        " Tab: next field | Enter: next step | Backspace: back step | q: quit "
    };
    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(Style::default().fg(Color::DarkGray));
    let p = Paragraph::new(help)
        .style(Style::default().fg(Color::DarkGray))
        .block(block);
    frame.render_widget(p, area);
}

fn draw_step(frame: &mut Frame, area: Rect, wizard: &Wizard) {
    let block = Block::default()
        .title(format!(" Step {}: {} ", wizard.step.index() + 1, wizard.step.title()))
        .borders(Borders::ALL)
        .border_style(Style::default().fg(Color::White));

    let inner = block.inner(area);
    frame.render_widget(block, area);

    match wizard.step {
        Step::Name => draw_name(frame, inner, wizard),
        Step::Physical => draw_physical(frame, inner, wizard),
        Step::Color => draw_color(frame, inner, wizard),
        Step::Typography => draw_typography(frame, inner, wizard),
        Step::Brief => draw_brief(frame, inner, wizard),
        Step::Domains => draw_domains(frame, inner, wizard),
        Step::References => draw_references(frame, inner, wizard),
        Step::Output => draw_output(frame, inner, wizard),
        Step::Review => draw_review(frame, inner, wizard),
    }
}

fn field_style(active: bool) -> Style {
    if active {
        Style::default().fg(Color::Cyan).bold()
    } else {
        Style::default().fg(Color::White)
    }
}

fn draw_name(frame: &mut Frame, area: Rect, wizard: &Wizard) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(3), Constraint::Length(3), Constraint::Min(0)])
        .split(area);

    let name_block = Block::default()
        .title(" Project Name ")
        .borders(Borders::ALL)
        .border_style(field_style(wizard.field_index == 0));
    let name_text = if wizard.field_index == 0 {
        format!("{}|", wizard.input_buf)
    } else {
        wizard.spec.name.clone()
    };
    frame.render_widget(Paragraph::new(name_text).block(name_block), chunks[0]);

    let type_items: Vec<ListItem> = crate::project::ProjectType::VARIANTS
        .iter()
        .enumerate()
        .map(|(i, v)| {
            let marker = if i == wizard.type_index { "> " } else { "  " };
            let style = if i == wizard.type_index {
                Style::default().fg(Color::Cyan).bold()
            } else {
                Style::default()
            };
            ListItem::new(format!("{marker}{v}")).style(style)
        })
        .collect();

    let type_block = Block::default()
        .title(" Project Type (Up/Down to select) ")
        .borders(Borders::ALL)
        .border_style(field_style(wizard.field_index == 1));
    let type_list = List::new(type_items).block(type_block);
    frame.render_widget(type_list, chunks[1].union(chunks[2]));
}

fn draw_physical(frame: &mut Frame, area: Rect, wizard: &Wizard) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Min(0),
        ])
        .split(area);

    let p = &wizard.spec.physical;

    let item_block = Block::default()
        .title(" Item Size (inches) ")
        .borders(Borders::ALL)
        .border_style(field_style(wizard.field_index == 0));
    frame.render_widget(
        Paragraph::new(format!("{:.2}\" x {:.2}\"", p.item_width_inches, p.item_height_inches))
            .block(item_block),
        chunks[0],
    );

    let stock_block = Block::default()
        .title(" Stock Size (inches) ")
        .borders(Borders::ALL)
        .border_style(field_style(wizard.field_index == 1));
    frame.render_widget(
        Paragraph::new(format!("{:.0}\" x {:.0}\"", p.stock_width_inches, p.stock_height_inches))
            .block(stock_block),
        chunks[1],
    );

    let sides_labels = ["single-sided", "double-sided"];
    let sides_str: String = sides_labels
        .iter()
        .enumerate()
        .map(|(i, l)| {
            if i == wizard.sides_index {
                format!("[{l}]")
            } else {
                format!(" {l} ")
            }
        })
        .collect::<Vec<_>>()
        .join("  ");
    let sides_block = Block::default()
        .title(" Sides (Left/Right) ")
        .borders(Borders::ALL)
        .border_style(field_style(wizard.field_index == 2));
    frame.render_widget(Paragraph::new(sides_str).block(sides_block), chunks[2]);

    let extra = format!(
        "Bleed: {:.3}\"    Quantity: {}",
        p.bleed_inches, p.quantity
    );
    let extra_block = Block::default()
        .title(" Bleed & Quantity ")
        .borders(Borders::ALL)
        .border_style(field_style(wizard.field_index == 3));
    frame.render_widget(Paragraph::new(extra).block(extra_block), chunks[3]);
}

fn draw_color(frame: &mut Frame, area: Rect, wizard: &Wizard) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Min(0),
        ])
        .split(area);

    let mode_labels = ["1-color", "2-color", "full-process (CMYK)"];
    let mode_str: String = mode_labels
        .iter()
        .enumerate()
        .map(|(i, l)| {
            if i == wizard.color_mode_index {
                format!("[{l}]")
            } else {
                format!(" {l} ")
            }
        })
        .collect::<Vec<_>>()
        .join("  ");

    let mode_block = Block::default()
        .title(" Color Mode (Left/Right) ")
        .borders(Borders::ALL)
        .border_style(field_style(wizard.field_index == 0));
    frame.render_widget(Paragraph::new(mode_str).block(mode_block), chunks[0]);

    let primary = &wizard.spec.color.primary;
    let primary_block = Block::default()
        .title(" Primary Color ")
        .borders(Borders::ALL)
        .border_style(field_style(wizard.field_index == 1));
    frame.render_widget(
        Paragraph::new(format!("{primary}")).block(primary_block),
        chunks[1],
    );

    let sec = wizard
        .spec
        .color
        .secondary
        .as_ref()
        .map_or("(none)".to_string(), |c| format!("{c}"));
    let sec_block = Block::default()
        .title(" Secondary Color ")
        .borders(Borders::ALL)
        .border_style(field_style(wizard.field_index == 2));
    frame.render_widget(Paragraph::new(sec).block(sec_block), chunks[2]);
}

fn draw_typography(frame: &mut Frame, area: Rect, wizard: &Wizard) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Length(3),
            Constraint::Min(0),
        ])
        .split(area);

    let labels = ["Primary Font", "Secondary Font", "Typography Notes"];
    let values = [
        &wizard.spec.typography.primary_font,
        &wizard.spec.typography.secondary_font,
        &wizard.spec.typography.notes,
    ];

    for (i, (chunk, (label, val))) in chunks
        .iter()
        .zip(labels.iter().zip(values.iter()))
        .enumerate()
    {
        let block = Block::default()
            .title(format!(" {label} "))
            .borders(Borders::ALL)
            .border_style(field_style(wizard.field_index == i));
        let text = if wizard.field_index == i {
            format!("{}|", wizard.input_buf)
        } else {
            val.to_string()
        };
        frame.render_widget(Paragraph::new(text).block(block), *chunk);
    }
}

fn draw_brief(frame: &mut Frame, area: Rect, wizard: &Wizard) {
    let block = Block::default()
        .title(" Creative Brief (type freely, Enter to submit) ")
        .borders(Borders::ALL)
        .border_style(field_style(true));
    let text = format!("{}|", wizard.input_buf);
    frame.render_widget(
        Paragraph::new(text).block(block).wrap(Wrap { trim: false }),
        area,
    );
}

fn draw_domains(frame: &mut Frame, area: Rect, wizard: &Wizard) {
    let items: Vec<ListItem> = wizard
        .available_domains()
        .iter()
        .enumerate()
        .map(|(i, d)| {
            let check = if wizard.domain_toggles[i] { "[x]" } else { "[ ]" };
            let marker = if i == wizard.field_index { "> " } else { "  " };
            let style = if i == wizard.field_index {
                Style::default().fg(Color::Cyan).bold()
            } else {
                Style::default()
            };
            ListItem::new(format!("{marker}{check} {d}")).style(style)
        })
        .collect();

    let block = Block::default()
        .title(" Domain Configs (Space to toggle) ")
        .borders(Borders::ALL);
    frame.render_widget(List::new(items).block(block), area);
}

fn draw_references(frame: &mut Frame, area: Rect, wizard: &Wizard) {
    let block = Block::default()
        .title(" Reference Paths (one per line) ")
        .borders(Borders::ALL)
        .border_style(field_style(true));
    let text = format!("{}|", wizard.input_buf);
    frame.render_widget(
        Paragraph::new(text).block(block).wrap(Wrap { trim: false }),
        area,
    );
}

fn draw_output(frame: &mut Frame, area: Rect, wizard: &Wizard) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(3), Constraint::Length(3), Constraint::Min(0)])
        .split(area);

    let formats = wizard.spec.output.formats.join(", ");
    let fmt_block = Block::default()
        .title(" Formats ")
        .borders(Borders::ALL);
    frame.render_widget(Paragraph::new(formats).block(fmt_block), chunks[0]);

    let impose = if wizard.spec.output.impose { "Yes" } else { "No" };
    let imp_block = Block::default()
        .title(" Impose on stock? ")
        .borders(Borders::ALL);
    frame.render_widget(Paragraph::new(impose).block(imp_block), chunks[1]);

    let notes_block = Block::default()
        .title(" Delivery Notes ")
        .borders(Borders::ALL)
        .border_style(field_style(true));
    let text = format!("{}|", wizard.input_buf);
    frame.render_widget(
        Paragraph::new(text).block(notes_block).wrap(Wrap { trim: false }),
        chunks[2],
    );
}

fn draw_review(frame: &mut Frame, area: Rect, wizard: &Wizard) {
    let s = &wizard.spec;
    let summary = format!(
        "Name:        {}\n\
         Type:        {}\n\
         Item size:   {:.2}\" x {:.2}\"\n\
         Stock:       {:.0}\" x {:.0}\"\n\
         Sides:       {}\n\
         Bleed:       {:.3}\"\n\
         Quantity:    {}\n\
         Color mode:  {}\n\
         Primary:     {}\n\
         Secondary:   {}\n\
         Fonts:       {} / {}\n\
         Domains:     {}\n\
         Refs:        {}\n\
         Formats:     {}\n\
         Impose:      {}\n\
         \n\
         Brief:\n{}\n\
         \n\
         Output dir:  {}/",
        s.name,
        s.project_type.label(),
        s.physical.item_width_inches,
        s.physical.item_height_inches,
        s.physical.stock_width_inches,
        s.physical.stock_height_inches,
        s.physical.sides.label(),
        s.physical.bleed_inches,
        s.physical.quantity,
        s.color.mode.label(),
        s.color.primary,
        s.color.secondary.as_ref().map_or("(none)".to_string(), |c| format!("{c}")),
        s.typography.primary_font,
        s.typography.secondary_font,
        s.domains.join(", "),
        s.references.len(),
        s.output.formats.join(", "),
        if s.output.impose { "yes" } else { "no" },
        s.brief,
        s.scaffold_dir(),
    );

    let status = if wizard.scaffolded {
        " [SCAFFOLDED] Press q to exit "
    } else {
        " Press Enter to scaffold project "
    };

    let block = Block::default()
        .title(format!(" Review {status}"))
        .borders(Borders::ALL)
        .border_style(if wizard.scaffolded {
            Style::default().fg(Color::Green)
        } else {
            Style::default().fg(Color::Yellow)
        });

    frame.render_widget(
        Paragraph::new(summary).block(block).wrap(Wrap { trim: false }),
        area,
    );
}
