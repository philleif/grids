mod project;
mod ui;
mod wizard;

use std::io;

use crossterm::{
    event::{self, Event, KeyCode, KeyModifiers},
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
    ExecutableCommand,
};
use ratatui::prelude::*;

use wizard::{Step, Wizard};

fn main() -> io::Result<()> {
    enable_raw_mode()?;
    io::stdout().execute(EnterAlternateScreen)?;

    let mut terminal = Terminal::new(CrosstermBackend::new(io::stdout()))?;
    let mut wizard = Wizard::new();
    wizard.load_step_buf();

    loop {
        terminal.draw(|frame| ui::draw(frame, &wizard))?;

        if let Event::Key(key) = event::read()? {
            if key.code == KeyCode::Char('c') && key.modifiers.contains(KeyModifiers::CONTROL) {
                break;
            }

            if key.code == KeyCode::Char('q')
                && wizard.step != Step::Brief
                && wizard.step != Step::References
                && wizard.step != Step::Output
                && wizard.step != Step::Name
                && wizard.step != Step::Typography
            {
                break;
            }

            match wizard.step {
                Step::Name => handle_name_input(&mut wizard, key.code),
                Step::Physical => handle_physical_input(&mut wizard, key.code),
                Step::Color => handle_color_input(&mut wizard, key.code),
                Step::Typography => handle_text_input(&mut wizard, key.code),
                Step::Brief => handle_multiline_input(&mut wizard, key.code),
                Step::Domains => handle_domains_input(&mut wizard, key.code),
                Step::References => handle_multiline_input(&mut wizard, key.code),
                Step::Output => handle_text_input(&mut wizard, key.code),
                Step::Review => handle_review_input(&mut wizard, key.code)?,
            }
        }
    }

    disable_raw_mode()?;
    io::stdout().execute(LeaveAlternateScreen)?;
    Ok(())
}

fn handle_name_input(wizard: &mut Wizard, code: KeyCode) {
    match code {
        KeyCode::Tab => wizard.next_field(),
        KeyCode::BackTab => wizard.prev_field(),
        KeyCode::Enter => wizard.advance(),
        KeyCode::Backspace if wizard.field_index == 0 => {
            wizard.input_buf.pop();
        }
        KeyCode::Char(c) if wizard.field_index == 0 => {
            wizard.input_buf.push(c);
        }
        KeyCode::Up if wizard.field_index == 1 => {
            if wizard.type_index > 0 {
                wizard.type_index -= 1;
            }
        }
        KeyCode::Down if wizard.field_index == 1 => {
            if wizard.type_index < project::ProjectType::VARIANTS.len() - 1 {
                wizard.type_index += 1;
            }
        }
        _ => {}
    }
}

fn handle_physical_input(wizard: &mut Wizard, code: KeyCode) {
    match code {
        KeyCode::Tab => wizard.next_field(),
        KeyCode::BackTab => wizard.prev_field(),
        KeyCode::Enter => wizard.advance(),
        KeyCode::Backspace => wizard.go_back(),
        KeyCode::Left if wizard.field_index == 2 => {
            wizard.sides_index = 0;
        }
        KeyCode::Right if wizard.field_index == 2 => {
            wizard.sides_index = 1;
        }
        _ => {}
    }
}

fn handle_color_input(wizard: &mut Wizard, code: KeyCode) {
    match code {
        KeyCode::Tab => wizard.next_field(),
        KeyCode::BackTab => wizard.prev_field(),
        KeyCode::Enter => wizard.advance(),
        KeyCode::Backspace => wizard.go_back(),
        KeyCode::Left if wizard.field_index == 0 => {
            if wizard.color_mode_index > 0 {
                wizard.color_mode_index -= 1;
            }
        }
        KeyCode::Right if wizard.field_index == 0 => {
            if wizard.color_mode_index < 2 {
                wizard.color_mode_index += 1;
            }
        }
        _ => {}
    }
}

fn handle_text_input(wizard: &mut Wizard, code: KeyCode) {
    match code {
        KeyCode::Tab => wizard.next_field(),
        KeyCode::BackTab => wizard.prev_field(),
        KeyCode::Enter => wizard.advance(),
        KeyCode::Esc => wizard.go_back(),
        KeyCode::Backspace => {
            wizard.input_buf.pop();
        }
        KeyCode::Char(c) => {
            wizard.input_buf.push(c);
        }
        _ => {}
    }
}

fn handle_multiline_input(wizard: &mut Wizard, code: KeyCode) {
    match code {
        KeyCode::Esc => wizard.advance(),
        KeyCode::Backspace => {
            wizard.input_buf.pop();
        }
        KeyCode::Enter => {
            wizard.input_buf.push('\n');
        }
        KeyCode::Char(c) => {
            wizard.input_buf.push(c);
        }
        _ => {}
    }
}

fn handle_domains_input(wizard: &mut Wizard, code: KeyCode) {
    match code {
        KeyCode::Tab | KeyCode::Down => wizard.next_field(),
        KeyCode::BackTab | KeyCode::Up => wizard.prev_field(),
        KeyCode::Char(' ') => wizard.toggle_domain(),
        KeyCode::Enter => wizard.advance(),
        KeyCode::Backspace => wizard.go_back(),
        _ => {}
    }
}

fn handle_review_input(wizard: &mut Wizard, code: KeyCode) -> io::Result<()> {
    match code {
        KeyCode::Backspace => wizard.go_back(),
        KeyCode::Enter if !wizard.scaffolded => {
            wizard.commit_current();
            match wizard.scaffold() {
                Ok(dir) => {
                    wizard.scaffolded = true;
                    eprintln!("Scaffolded project at: {dir}/");
                }
                Err(e) => {
                    eprintln!("Error scaffolding: {e}");
                }
            }
        }
        _ => {}
    }
    Ok(())
}
