use std::{
    collections::{HashMap, HashSet},
    env,
    ffi::c_void,
    fs,
    io::{Read, Write},
    net::{SocketAddr, TcpStream},
    path::{Path, PathBuf},
    process::Command,
    sync::mpsc::{self, Receiver, Sender},
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

use eframe::egui;
#[cfg(not(target_arch = "wasm32"))]
use raw_window_handle::{HasRawWindowHandle, RawWindowHandle};
use serde_json::Value;

#[cfg(windows)]
use winapi::{
    shared::minwindef::FILETIME,
    shared::windef::HWND,
    um::{
        minwinbase::SYSTEMTIME,
        timezoneapi::{FileTimeToSystemTime, SystemTimeToTzSpecificLocalTime},
        winuser::{
            CreateIcon, ReleaseCapture, SendMessageW, ICON_BIG, ICON_SMALL, WM_NCLBUTTONDOWN,
            WM_SETICON,
        },
    },
};

const PANEL_TITLE: &str = "CENTRO DE COMANDO // HERMES";
static SILKSCREEN: &[u8] = include_bytes!("../assets/Silkscreen-Regular.ttf");

// These are layout measurements, not arbitrary viewport defaults.  The
// Claude grid needs 260 px for sprite + active marker + the 186 px email
// sample (a 29-character address, truncated), and 300 px for each quota cell:
// 84 px bar + 42 px percentage + 10 px gap + 4 px separator + the measured
// countdown and exact reset clock.  Codex uses the same quota cells plus a
// 150 px plan column.  The section frame adds 32 px (2 px borders and 14 px
// horizontal inner margins on both sides).
// OpenCodex uses 340 px for the account id and marker plus the same two quota
// cells; its 960 px content width is included in the widest-section calculation.
const TITLE_BAR_HEIGHT: f32 = 36.0;
const SECTION_HORIZONTAL_OVERHEAD: f32 = 32.0;
const CLAUDE_ACCOUNT_WIDTH: f32 = 260.0;
const CLAUDE_QUOTA_WIDTH: f32 = 300.0;
const CLAUDE_GRID_GAPS: f32 = 20.0;
const CLAUDE_CONTENT_WIDTH: f32 =
    CLAUDE_ACCOUNT_WIDTH + CLAUDE_GRID_GAPS + CLAUDE_QUOTA_WIDTH * 2.0;
const CODEX_ACCOUNT_WIDTH: f32 = 300.0;
const CODEX_PLAN_WIDTH: f32 = 150.0;
const CODEX_MODEL_WIDTH: f32 = 140.0;
const CODEX_EFFORT_WIDTH: f32 = 90.0;
const CODEX_QUOTA_WIDTH: f32 = 300.0;
const CODEX_GRID_GAPS: f32 = 50.0;
const CODEX_CONTENT_WIDTH: f32 = CODEX_ACCOUNT_WIDTH
    + CODEX_PLAN_WIDTH
    + CODEX_MODEL_WIDTH
    + CODEX_EFFORT_WIDTH
    + CODEX_GRID_GAPS
    + CODEX_QUOTA_WIDTH * 2.0;
const OPENCODEX_ACCOUNT_WIDTH: f32 = 340.0;
const OPENCODEX_QUOTA_WIDTH: f32 = 300.0;
const OPENCODEX_GRID_GAPS: f32 = 20.0;
const OPENCODEX_CONTENT_WIDTH: f32 =
    OPENCODEX_ACCOUNT_WIDTH + OPENCODEX_GRID_GAPS + OPENCODEX_QUOTA_WIDTH * 2.0;
const INITIAL_CONTENT_WIDTH: f32 = if CLAUDE_CONTENT_WIDTH > CODEX_CONTENT_WIDTH {
    if CLAUDE_CONTENT_WIDTH > OPENCODEX_CONTENT_WIDTH {
        CLAUDE_CONTENT_WIDTH
    } else {
        OPENCODEX_CONTENT_WIDTH
    }
} else if CODEX_CONTENT_WIDTH > OPENCODEX_CONTENT_WIDTH {
    CODEX_CONTENT_WIDTH
} else {
    OPENCODEX_CONTENT_WIDTH
};
const INITIAL_INNER_WIDTH: f32 = SECTION_HORIZONTAL_OVERHEAD + INITIAL_CONTENT_WIDTH + 8.0;
const INITIAL_INNER_HEIGHT: f32 = 618.0;
const MIN_INNER_WIDTH: f32 = 520.0;
const MIN_INNER_HEIGHT: f32 = 320.0;
const MIN_CLAUDE_ACCOUNT_WIDTH: f32 = 220.0;
const MIN_CLAUDE_QUOTA_WIDTH: f32 = 124.0;
const MIN_BAR_WIDTH: f32 = 32.0;
const PERCENT_WIDTH: f32 = 42.0;
const ACCOUNT_SPRITE_SIZE: f32 = 40.0;
const ACCOUNT_CURSOR_WIDTH: f32 = 10.0;
const ARENA_PLATE_HEIGHT: f32 = 42.0;
const ARENA_SPRITE_SIZE: f32 = 112.0;
const ARENA_DELEGATION_HEIGHT: f32 = 38.0;
const ARENA_MIN_PLATE_WIDTH: f32 = 150.0;
const ARENA_MIN_SIDE_WIDTH: f32 = 160.0;
const ARENA_MIN_DELEGATION_WIDTH: f32 = 120.0;
const ARENA_COLUMN_GAP: f32 = 12.0;
const ARENA_PLATE_MAX_WIDTH: f32 = 360.0;
const HT_LEFT: usize = 10;
const HT_RIGHT: usize = 11;
const HT_TOP: usize = 12;
const HT_TOP_LEFT: usize = 13;
const HT_TOP_RIGHT: usize = 14;
const HT_BOTTOM: usize = 15;
const HT_BOTTOM_LEFT: usize = 16;
const HT_BOTTOM_RIGHT: usize = 17;

fn ground() -> egui::Color32 {
    egui::Color32::from_rgb(0x10, 0x14, 0x0F)
}

fn panel() -> egui::Color32 {
    egui::Color32::from_rgb(0x1A, 0x21, 0x18)
}

fn border() -> egui::Color32 {
    egui::Color32::from_rgb(0x4A, 0x5D, 0x3F)
}

fn text_color() -> egui::Color32 {
    egui::Color32::from_rgb(0xC8, 0xD6, 0xBC)
}

fn dim_color() -> egui::Color32 {
    egui::Color32::from_rgb(0x6B, 0x7D, 0x5E)
}

fn hp_ok() -> egui::Color32 {
    egui::Color32::from_rgb(0x7F, 0xB0, 0x69)
}

fn hp_mid() -> egui::Color32 {
    egui::Color32::from_rgb(0xE5, 0xB4, 0x51)
}

fn hp_low() -> egui::Color32 {
    egui::Color32::from_rgb(0xD4, 0x5D, 0x5D)
}

fn set_icon_block(rgba: &mut [u8], x: usize, y: usize, color: egui::Color32) {
    for pixel_y in y * 2..y * 2 + 2 {
        for pixel_x in x * 2..x * 2 + 2 {
            let offset = (pixel_y * 32 + pixel_x) * 4;
            rgba[offset..offset + 4].copy_from_slice(&color.to_array());
        }
    }
}

fn icone() -> egui::IconData {
    const PIXEL_ART: [&str; 16] = [
        ".....KKKKKK.....",
        "...KKRRRRRRKK...",
        "..KKRRRRRRRRKK..",
        ".KKRRRRRRRRRRKK.",
        ".KKRRRRRRRRRRKK.",
        ".KKRRRRRRRRRRKK.",
        ".KKRRRRRRRRRRKK.",
        ".KKKKKKKKKKKKKK.",
        ".KKKKKKKKKKKKKK.",
        ".KKWWWWWWWWWWKK.",
        ".KKWWWWWWWWWWKK.",
        ".KKWWWWWWWWWWKK.",
        ".KKWWWWWWWWWWKK.",
        "..KKWWWWWWWWKK..",
        "...KKWWWWWWKK...",
        ".....KKKKKK.....",
    ];
    let mut rgba = vec![0; 32 * 32 * 4];

    for (y, row) in PIXEL_ART.iter().enumerate() {
        for (x, pixel) in row.bytes().enumerate() {
            let color = match pixel {
                b'K' => ground(),
                b'R' => hp_low(),
                b'W' => text_color(),
                _ => continue,
            };
            set_icon_block(&mut rgba, x, y, color);
        }
    }

    for &(x, y) in &[
        (6, 5),
        (7, 5),
        (8, 5),
        (9, 5),
        (5, 6),
        (10, 6),
        (5, 7),
        (10, 7),
        (5, 8),
        (10, 8),
        (5, 9),
        (10, 9),
        (6, 10),
        (7, 10),
        (8, 10),
        (9, 10),
    ] {
        set_icon_block(&mut rgba, x, y, ground());
    }
    for &(x, y) in &[
        (7, 6),
        (8, 6),
        (6, 7),
        (7, 7),
        (8, 7),
        (9, 7),
        (6, 8),
        (7, 8),
        (8, 8),
        (9, 8),
        (7, 9),
        (8, 9),
    ] {
        set_icon_block(&mut rgba, x, y, text_color());
    }

    egui::IconData {
        rgba,
        width: 32,
        height: 32,
    }
}

struct Snapshot {
    status: Result<Value, String>,
    cswap: Result<Value, String>,
    fixacao: Option<Value>,
    codex_last_used_profile: Option<String>,
    codex_active_profile: Option<String>,
    codex_profile_settings: Vec<(String, Option<String>, Option<String>)>,
    opencodex: Option<OpenCodexSnapshot>,
}

struct OpenCodexSnapshot {
    default_provider: Option<String>,
    mode: Option<String>,
    default_model: Option<String>,
    accounts: Vec<OpenCodexAccount>,
}

struct OpenCodexAccount {
    id: String,
    weekly_percent: Option<f64>,
    short_percent: Option<f64>,
    weekly_reset_at: Option<f64>,
    short_reset_at: Option<f64>,
    active: bool,
}

struct PanelApp {
    receiver: Receiver<Snapshot>,
    status: Option<Result<Value, String>>,
    cswap: Option<Result<Value, String>>,
    fixacao: Option<Value>,
    codex_last_used_profile: Option<String>,
    codex_active_profile: Option<String>,
    codex_profile_settings: Vec<(String, Option<String>, Option<String>)>,
    opencodex: Option<OpenCodexSnapshot>,
    pokemon: PokemonCache,
    poke_mode: bool,
    hwnd: Option<*mut c_void>,
}

#[derive(Clone)]
struct PokemonInfo {
    email: String,
    number: u16,
    name: String,
    provider: String,
    plan: Option<String>,
}

#[derive(Clone)]
struct ClaudeAccountTarget {
    number: i64,
    email: String,
    disabled: bool,
}

struct PokemonCache {
    directory: PathBuf,
    map: HashMap<String, PokemonInfo>,
    textures: HashMap<(u16, bool), egui::TextureHandle>,
    attempted: HashSet<(u16, bool)>,
}

impl PokemonCache {
    fn new() -> Self {
        let directory = claude_config_dir()
            .map(|path| path.join("poke-cache"))
            .unwrap_or_else(|| PathBuf::from("poke-cache"));
        let mut map = HashMap::new();
        if let Ok(contents) = fs::read_to_string(directory.join("mapa.json")) {
            if let Ok(Value::Object(entries)) = serde_json::from_str::<Value>(&contents) {
                for (email, entry) in entries {
                    let number = entry
                        .get("numero")
                        .and_then(Value::as_u64)
                        .filter(|number| (1..=1025).contains(number));
                    let name = entry.get("nome").and_then(Value::as_str);
                    if let (Some(number), Some(name)) = (number, name) {
                        let display_email = entry
                            .get("email")
                            .and_then(Value::as_str)
                            .unwrap_or(email.as_str())
                            .to_owned();
                        let provider = entry
                            .get("provedor")
                            .and_then(Value::as_str)
                            .unwrap_or("claude")
                            .to_owned();
                        let plan = entry
                            .get("plano")
                            .and_then(Value::as_str)
                            .map(str::to_owned);
                        map.insert(
                            email,
                            PokemonInfo {
                                email: display_email,
                                number: number as u16,
                                name: name.to_owned(),
                                provider,
                                plan,
                            },
                        );
                    }
                }
            }
        }
        Self {
            directory,
            map,
            textures: HashMap::new(),
            attempted: HashSet::new(),
        }
    }

    fn info(&self, provider: &str, email: &str) -> Option<PokemonInfo> {
        self.map
            .values()
            .find(|info| info.provider == provider && info.email.eq_ignore_ascii_case(email))
            .cloned()
    }

    fn texture(
        &mut self,
        context: &egui::Context,
        provider: &str,
        email: &str,
        back: bool,
    ) -> Option<&egui::TextureHandle> {
        let info = self.info(provider, email)?;
        let key = (info.number, back);
        if !self.attempted.insert(key) {
            return self.textures.get(&key);
        }
        let side = if back { "costas" } else { "frente" };
        let path = self.directory.join(format!("{}-{side}.png", info.number));
        let bytes = fs::read(path).ok()?;
        let decoded = image::load_from_memory(&bytes).ok()?.to_rgba8();
        let size = [decoded.width() as usize, decoded.height() as usize];
        let color_image = egui::ColorImage::from_rgba_unmultiplied(size, decoded.as_raw());
        let texture = context.load_texture(
            format!("pokemon-{}-{side}", info.number),
            color_image,
            egui::TextureOptions::NEAREST,
        );
        self.textures.insert(key, texture);
        self.textures.get(&key)
    }
}

impl PanelApp {
    fn new(creation_context: &eframe::CreationContext<'_>) -> Self {
        configure_context(&creation_context.egui_ctx);
        let (sender, receiver) = mpsc::channel();
        let repo = find_repo();
        thread::spawn(move || refresh_loop(sender, repo));
        let hwnd = native_window_handle(creation_context);
        set_taskbar_icon(hwnd);

        Self {
            receiver,
            status: None,
            cswap: None,
            fixacao: None,
            codex_last_used_profile: None,
            codex_active_profile: None,
            codex_profile_settings: Vec::new(),
            opencodex: None,
            pokemon: PokemonCache::new(),
            poke_mode: load_poke_mode(),
            hwnd,
        }
    }

    fn receive_latest(&mut self) {
        while let Ok(snapshot) = self.receiver.try_recv() {
            self.status = Some(snapshot.status);
            self.cswap = Some(snapshot.cswap);
            self.fixacao = snapshot.fixacao;
            self.codex_last_used_profile = snapshot.codex_last_used_profile;
            self.codex_active_profile = snapshot.codex_active_profile;
            self.codex_profile_settings = snapshot.codex_profile_settings;
            self.opencodex = snapshot.opencodex;
        }
    }
}

fn configure_context(context: &egui::Context) {
    let mut fonts = egui::FontDefinitions::default();
    fonts.font_data.insert(
        "silkscreen".to_owned(),
        egui::FontData::from_static(SILKSCREEN),
    );
    fonts
        .families
        .entry(egui::FontFamily::Name("silkscreen".into()))
        .or_default()
        .insert(0, "silkscreen".to_owned());
    context.set_fonts(fonts);

    let mut style = (*context.style()).clone();
    style.spacing.item_spacing = egui::vec2(10.0, 8.0);
    style.spacing.window_margin = egui::Margin::same(12.0);
    style.spacing.button_padding = egui::vec2(6.0, 3.0);
    style.visuals = terminal_visuals();
    context.set_style(style);
}

fn terminal_visuals() -> egui::Visuals {
    let mut visuals = egui::Visuals::dark();
    visuals.override_text_color = Some(text_color());
    visuals.panel_fill = ground();
    visuals.window_fill = panel();
    visuals.extreme_bg_color = ground();
    visuals.faint_bg_color = panel();
    visuals.code_bg_color = panel();
    visuals.hyperlink_color = text_color();
    visuals.warn_fg_color = dim_color();
    visuals.error_fg_color = text_color();
    visuals.window_rounding = egui::Rounding::same(0.0);
    visuals.window_stroke = egui::Stroke::new(2.0_f32, border());
    visuals.selection.bg_fill = border();
    visuals.selection.stroke = egui::Stroke::new(1.0_f32, text_color());

    for widget in [
        &mut visuals.widgets.noninteractive,
        &mut visuals.widgets.inactive,
        &mut visuals.widgets.hovered,
        &mut visuals.widgets.active,
        &mut visuals.widgets.open,
    ] {
        widget.rounding = egui::Rounding::same(0.0);
        widget.bg_stroke = egui::Stroke::new(1.0_f32, border());
        widget.fg_stroke = egui::Stroke::new(1.0_f32, text_color());
        widget.expansion = 0.0;
    }
    visuals.widgets.noninteractive.bg_stroke = egui::Stroke::NONE;
    visuals.widgets.inactive.weak_bg_fill = ground();
    visuals.widgets.hovered.weak_bg_fill = border();
    visuals.widgets.active.weak_bg_fill = hp_ok();
    visuals.widgets.open.weak_bg_fill = border();
    visuals
}

impl eframe::App for PanelApp {
    fn update(&mut self, context: &egui::Context, _frame: &mut eframe::Frame) {
        // A janela nasce dimensionada em pixels, mas o egui desenha em pontos. Com a
        // escala do Windows em 125% o conteudo saia 25% maior que o quadro em qualquer
        // largura. Fixar 1 ponto = 1 pixel alinha os dois e deixa a Silkscreen, que e
        // uma fonte pixelada, nitida em vez de interpolada. Precisa ser a cada frame:
        // o eframe reimpoe a escala nativa e sobrescreve o ajuste feito na inicializacao.

        self.receive_latest();
        context.request_repaint_after(Duration::from_millis(100));

        show_title_bar(context, &mut self.poke_mode);

        egui::CentralPanel::default().show(context, |ui| {
            let central_panel_margin =
                ui.spacing().window_margin.left + ui.spacing().window_margin.right;
            // A largura vem do screen_rect, nao do clip_rect: quando o conteudo nao
            // cabe, a ScrollArea infla o clip_rect e o layout passa a acreditar que
            // tem MAIS espaco justo quando tem menos — realimentacao que impedia as
            // secoes de encolher. O screen_rect e a janela e nao sofre disso.
            // O eframe reporta screen_rect em PIXELS fisicos, mas o egui desenha em
            // pontos (aqui 1 ponto = 1.25 px). Sem dividir pela escala o layout acha
            // que tem 700 pontos numa janela de 700 px e estoura 25% em toda largura.
            // Nao usar clip_rect: quando o conteudo nao cabe, a ScrollArea o infla e a
            // realimentacao impede as secoes de encolher.
            let content_width = (context.screen_rect().width() / context.pixels_per_point()
                - central_panel_margin
                - SECTION_HORIZONTAL_OVERHEAD
                - ui.spacing().scroll.allocated_width())
            .max(0.0);
            egui::ScrollArea::vertical()
                .auto_shrink([false, false])
                .show(ui, |ui| {
                    show_command_center(
                        ui,
                        content_width,
                        self.status.as_ref(),
                        self.cswap.as_ref(),
                        self.codex_active_profile.as_deref(),
                        &self.codex_profile_settings,
                        &mut self.pokemon,
                        context,
                        self.poke_mode,
                    );
                    ui.add_space(12.0);
                    show_command_line(ui, content_width, self.cswap.as_ref());
                    ui.add_space(12.0);
                    show_running_jobs(ui, content_width, self.status.as_ref());
                    ui.add_space(12.0);

                    show_claude(
                        ui,
                        content_width,
                        self.cswap.as_ref(),
                        self.fixacao.as_ref(),
                        &mut self.pokemon,
                        context,
                        self.poke_mode,
                    );
                    ui.add_space(12.0);

                    show_codex(
                        ui,
                        content_width,
                        self.status.as_ref(),
                        self.codex_last_used_profile.as_deref(),
                        self.codex_active_profile.as_deref(),
                        &self.codex_profile_settings,
                        &mut self.pokemon,
                        context,
                        self.poke_mode,
                    );
                    ui.add_space(8.0);
                    if let Some(opencodex) = self.opencodex.as_ref() {
                        show_opencodex(ui, content_width, opencodex);
                        ui.add_space(8.0);
                    }
                    show_free_tier(ui, content_width, self.status.as_ref());
                    ui.add_space(8.0);
                    show_context(ui, content_width, self.status.as_ref());
                });
        });

        show_resize_handles(context, self.hwnd);
    }
}

#[cfg(not(target_arch = "wasm32"))]
fn native_window_handle(creation_context: &eframe::CreationContext<'_>) -> Option<*mut c_void> {
    match creation_context.raw_window_handle() {
        RawWindowHandle::Win32(handle) if !handle.hwnd.is_null() => Some(handle.hwnd),
        _ => None,
    }
}

#[cfg(target_arch = "wasm32")]
fn native_window_handle(_creation_context: &eframe::CreationContext<'_>) -> Option<*mut c_void> {
    None
}

#[cfg(windows)]
fn set_taskbar_icon(hwnd: Option<*mut c_void>) {
    let Some(hwnd) = hwnd.filter(|hwnd| !hwnd.is_null()) else {
        return;
    };
    let icon = icone();
    let width = icon.width as usize;
    let height = icon.height as usize;
    let mut xor = vec![0_u8; icon.rgba.len()];
    // CreateIcon espera BGRA bottom-up; egui armazena RGBA top-down.
    for y in 0..height {
        for x in 0..width {
            let from = (y * width + x) * 4;
            let to = ((height - y - 1) * width + x) * 4;
            xor[to..to + 4].copy_from_slice(&[
                icon.rgba[from + 2],
                icon.rgba[from + 1],
                icon.rgba[from],
                icon.rgba[from + 3],
            ]);
        }
    }
    let and_stride = ((width + 15) / 16) * 2;
    let and = vec![0_u8; and_stride * height];
    let handle = unsafe {
        CreateIcon(
            std::ptr::null_mut(),
            width as i32,
            height as i32,
            1,
            32,
            and.as_ptr(),
            xor.as_ptr(),
        )
    };
    if handle.is_null() {
        return;
    }
    unsafe {
        SendMessageW(
            hwnd as HWND,
            WM_SETICON,
            ICON_SMALL as usize,
            handle as isize,
        );
        SendMessageW(hwnd as HWND, WM_SETICON, ICON_BIG as usize, handle as isize);
    }
}

#[cfg(not(windows))]
fn set_taskbar_icon(_hwnd: Option<*mut c_void>) {}

#[cfg(windows)]
fn begin_native_resize(hwnd: Option<*mut c_void>, hit_test: usize) {
    if let Some(hwnd) = hwnd.filter(|hwnd| !hwnd.is_null()) {
        unsafe {
            ReleaseCapture();
            SendMessageW(hwnd as HWND, WM_NCLBUTTONDOWN, hit_test, 0);
        }
    }
}

#[cfg(not(windows))]
fn begin_native_resize(_hwnd: Option<*mut c_void>, _hit_test: usize) {}

fn show_resize_handles(context: &egui::Context, hwnd: Option<*mut c_void>) {
    let screen = context.screen_rect();
    let edge = 6.0;
    let corner = 12.0;
    let left = screen.left();
    let right = screen.right();
    let top = screen.top();
    let bottom = screen.bottom();

    let zones = [
        (
            egui::Rect::from_min_max(
                egui::pos2(left + corner, top),
                egui::pos2(right - corner, top + edge),
            ),
            HT_TOP,
            egui::CursorIcon::ResizeVertical,
            "north",
        ),
        (
            egui::Rect::from_min_max(
                egui::pos2(left + corner, bottom - edge),
                egui::pos2(right - corner, bottom),
            ),
            HT_BOTTOM,
            egui::CursorIcon::ResizeVertical,
            "south",
        ),
        (
            egui::Rect::from_min_max(
                egui::pos2(left, top + corner),
                egui::pos2(left + edge, bottom - corner),
            ),
            HT_LEFT,
            egui::CursorIcon::ResizeHorizontal,
            "west",
        ),
        (
            egui::Rect::from_min_max(
                egui::pos2(right - edge, top + corner),
                egui::pos2(right, bottom - corner),
            ),
            HT_RIGHT,
            egui::CursorIcon::ResizeHorizontal,
            "east",
        ),
        (
            egui::Rect::from_min_max(
                egui::pos2(left, top),
                egui::pos2(left + corner, top + corner),
            ),
            HT_TOP_LEFT,
            egui::CursorIcon::ResizeNwSe,
            "north_west",
        ),
        (
            egui::Rect::from_min_max(
                egui::pos2(right - corner, top),
                egui::pos2(right, top + corner),
            ),
            HT_TOP_RIGHT,
            egui::CursorIcon::ResizeNeSw,
            "north_east",
        ),
        (
            egui::Rect::from_min_max(
                egui::pos2(left, bottom - corner),
                egui::pos2(left + corner, bottom),
            ),
            HT_BOTTOM_LEFT,
            egui::CursorIcon::ResizeNeSw,
            "south_west",
        ),
        (
            egui::Rect::from_min_max(
                egui::pos2(right - corner, bottom - corner),
                egui::pos2(right, bottom),
            ),
            HT_BOTTOM_RIGHT,
            egui::CursorIcon::ResizeNwSe,
            "south_east",
        ),
    ];

    for (rect, hit_test, cursor, id) in zones {
        egui::Area::new(egui::Id::new(("window_resize_handle", id)))
            .order(egui::Order::Foreground)
            .fixed_pos(rect.min)
            .movable(false)
            .show(context, |ui| {
                ui.set_min_size(rect.size());
                ui.set_clip_rect(rect);

                let response = ui.interact(
                    rect,
                    ui.id().with("interaction"),
                    egui::Sense::click_and_drag(),
                );
                if response.hovered() || response.dragged() {
                    context.set_cursor_icon(cursor);
                }
                if response.drag_started() {
                    begin_native_resize(hwnd, hit_test);
                }
            });
    }
}

fn show_title_bar(context: &egui::Context, poke_mode: &mut bool) {
    egui::TopBottomPanel::top("custom_title_bar")
        .exact_height(TITLE_BAR_HEIGHT)
        .frame(
            egui::Frame::none()
                .fill(panel())
                .stroke(egui::Stroke::new(2.0_f32, border()))
                .inner_margin(egui::Margin::symmetric(10.0, 5.0)),
        )
        .show(context, |ui| {
            ui.horizontal(|ui| {
                let controls_width = 68.0 + 24.0 + 24.0 + ui.spacing().item_spacing.x * 2.0;
                let drag_width = (ui.available_width() - controls_width).max(40.0);
                let (rect, response) = ui.allocate_exact_size(
                    egui::vec2(drag_width, 24.0),
                    egui::Sense::click_and_drag(),
                );
                ui.painter().text(
                    rect.left_center(),
                    egui::Align2::LEFT_CENTER,
                    PANEL_TITLE,
                    pixel_font(11.0),
                    text_color(),
                );
                if response.drag_started() {
                    context.send_viewport_cmd(egui::ViewportCommand::StartDrag);
                }

                if ui
                    .add_sized(
                        [68.0, 24.0],
                        egui::Button::new(pixel_text(
                            if *poke_mode { "POKE" } else { "SOBRIO" },
                            9.0,
                        )),
                    )
                    .clicked()
                {
                    *poke_mode = !*poke_mode;
                    save_poke_mode(*poke_mode);
                }
                if ui
                    .add_sized([24.0, 24.0], egui::Button::new(data_text("-")))
                    .clicked()
                {
                    context.send_viewport_cmd(egui::ViewportCommand::Minimized(true));
                }
                if ui
                    .add_sized([24.0, 24.0], egui::Button::new(data_text("X")))
                    .clicked()
                {
                    context.send_viewport_cmd(egui::ViewportCommand::Close);
                }
            });
        });
}

fn refresh_loop(sender: Sender<Snapshot>, repo: PathBuf) {
    loop {
        let status = read_status(&repo);
        let cswap = read_cswap();
        let fixacao = claude_config_dir()
            .map(|directory| directory.join("cswap-fixada.json"))
            .and_then(|path| read_json_file(&path));
        let codex_last_used_profile = read_latest_codex_profile();
        let codex_active_profile = read_codex_active_profile();
        let codex_profile_settings = codex_profile_directories()
            .into_iter()
            .filter_map(|directory| {
                let profile = directory.file_name()?.to_str()?.to_owned();
                let config = fs::read_to_string(directory.join("config.toml")).ok()?;
                Some((
                    profile,
                    read_config_scalar(&config, "model"),
                    read_config_scalar(&config, "model_reasoning_effort"),
                ))
            })
            .collect();
        let opencodex = read_opencodex();
        if sender
            .send(Snapshot {
                status,
                cswap,
                fixacao,
                codex_last_used_profile,
                codex_active_profile,
                codex_profile_settings,
                opencodex,
            })
            .is_err()
        {
            break;
        }
        thread::sleep(Duration::from_secs(2));
    }
}

fn read_status(repo: &Path) -> Result<Value, String> {
    let script = repo.join("skills").join("codex").join("status.py");
    let output = match Command::new("python")
        .arg(&script)
        .arg("--json")
        .current_dir(repo)
        .output()
    {
        Ok(output) => output,
        Err(error) => return Err(format!("status.py: {error}")),
    };

    if !output.status.success() {
        return Err(format!(
            "status.py saiu com erro: {}",
            process_output_message(&output)
        ));
    }

    let stdout = match String::from_utf8(output.stdout) {
        Ok(stdout) => stdout,
        Err(error) => return Err(format!("status.py retornou UTF-8 inválido: {error}")),
    };
    serde_json::from_str(&stdout)
        .map_err(|error| format!("status.py retornou JSON inválido: {error}"))
}

fn read_cswap() -> Result<Value, String> {
    let executable = cswap_executable().ok_or_else(|| "cswap não encontrado".to_owned())?;

    let output = match Command::new(&executable).arg("list").arg("--json").output() {
        Ok(output) => output,
        Err(error) => return Err(format!("cswap: {error}")),
    };
    if !output.status.success() {
        return Err(format!(
            "cswap saiu com erro: {}",
            process_output_message(&output)
        ));
    }

    let stdout = match String::from_utf8(output.stdout) {
        Ok(stdout) => stdout,
        Err(error) => return Err(format!("cswap retornou UTF-8 inválido: {error}")),
    };
    serde_json::from_str(&stdout).map_err(|error| format!("cswap retornou JSON inválido: {error}"))
}

fn cswap_executable() -> Option<PathBuf> {
    let home = env::var_os("HOME").or_else(|| env::var_os("USERPROFILE"))?;
    let executable = PathBuf::from(home)
        .join(".local")
        .join("bin")
        .join("cswap.exe");
    executable.is_file().then_some(executable)
}

fn home_directory() -> Option<PathBuf> {
    env::var_os("HOME")
        .or_else(|| env::var_os("USERPROFILE"))
        .map(PathBuf::from)
}

fn claude_config_dir() -> Option<PathBuf> {
    env::var_os("CLAUDE_CONFIG_DIR")
        .map(PathBuf::from)
        .or_else(|| home_directory().map(|path| path.join(".claude")))
}

fn read_json_file(path: &Path) -> Option<Value> {
    let contents = fs::read_to_string(path).ok()?;
    serde_json::from_str(&contents).ok()
}

fn fixacao_tem_numero(fixacao: Option<&Value>, number: Option<i64>) -> bool {
    let Some(number) = number else {
        return false;
    };
    fixacao
        .and_then(|value| value.get("desabilitou"))
        .and_then(Value::as_array)
        .is_some_and(|numbers| numbers.iter().any(|value| value.as_i64() == Some(number)))
}

fn email_esta_fixado(fixacao: Option<&Value>, email: &str) -> bool {
    fixacao
        .and_then(|value| value.get("email"))
        .and_then(Value::as_str)
        .is_some_and(|fixed_email| fixed_email.eq_ignore_ascii_case(email))
}

fn run_cswap_number(executable: &Path, action: &str, number: i64) -> bool {
    match Command::new(executable)
        .arg(action)
        .arg(number.to_string())
        .output()
    {
        Ok(output) if output.status.success() => true,
        Ok(output) => {
            eprintln!(
                "cswap {action} {number}: {}",
                process_output_message(&output)
            );
            false
        }
        Err(error) => {
            eprintln!("cswap {action} {number}: {error}");
            false
        }
    }
}

fn write_fixacao(path: &Path, email: &str, numbers: &[i64]) -> bool {
    let Some(directory) = path.parent() else {
        return false;
    };
    if fs::create_dir_all(directory).is_err() {
        return false;
    }
    let desde = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0);
    let contents = serde_json::json!({
        "email": email,
        "desde": desde,
        "desabilitou": numbers,
        "ultimo_pct": null,
    })
    .to_string()
        + "\n";
    let temporary = path.with_extension(format!("json.{}.tmp", std::process::id()));
    if fs::write(&temporary, contents).is_err() {
        return false;
    }
    if fs::rename(&temporary, path).is_err() {
        let _ = fs::remove_file(&temporary);
        return false;
    }
    true
}

fn fix_claude_account(number: i64, email: String, accounts: Vec<ClaudeAccountTarget>) {
    let Some(executable) = cswap_executable() else {
        return;
    };
    let Some(directory) = claude_config_dir() else {
        return;
    };
    let state_path = directory.join("cswap-fixada.json");

    if state_path.is_file() {
        if let Some(fixacao) = read_json_file(&state_path) {
            if let Some(numbers) = fixacao.get("desabilitou").and_then(Value::as_array) {
                for number in numbers.iter().filter_map(Value::as_i64) {
                    run_cswap_number(&executable, "enable", number);
                }
            }
        }
        let _ = fs::remove_file(&state_path);
    }

    let switched = match Command::new(&executable)
        .arg("switch")
        .arg(number.to_string())
        .output()
    {
        Ok(output) if output.status.success() => true,
        Ok(output) => {
            eprintln!("cswap switch {number}: {}", process_output_message(&output));
            false
        }
        Err(error) => {
            eprintln!("cswap switch {number}: {error}");
            false
        }
    };
    if !switched {
        return;
    }

    let numbers = accounts
        .iter()
        .filter(|account| {
            account.number != number
                && !account.email.eq_ignore_ascii_case(&email)
                && !account.disabled
        })
        .map(|account| account.number)
        .collect::<Vec<_>>();
    if !write_fixacao(&state_path, &email, &numbers) {
        eprintln!("cswap fixacao: nao foi possivel gravar o estado");
        return;
    }
    for number in numbers {
        run_cswap_number(&executable, "disable", number);
    }
}

fn read_http_response(stream: &mut TcpStream) -> Option<(String, Vec<u8>)> {
    const MAX_HEADER_BYTES: usize = 64 * 1024;
    let mut response = Vec::new();
    let header_end;
    let mut buffer = [0_u8; 1024];
    loop {
        let read = stream.read(&mut buffer).ok()?;
        if read == 0 {
            return None;
        }
        response.extend_from_slice(&buffer[..read]);
        if let Some(end) = response.windows(4).position(|window| window == b"\r\n\r\n") {
            header_end = end;
            break;
        }
        if response.len() > MAX_HEADER_BYTES {
            return None;
        }
    }

    let headers = String::from_utf8(response[..header_end].to_vec()).ok()?;
    let body_start = header_end + 4;
    let content_length = headers.lines().find_map(|line| {
        let (name, value) = line.split_once(':')?;
        name.trim()
            .eq_ignore_ascii_case("content-length")
            .then(|| value.trim().parse::<usize>().ok())
            .flatten()
    });
    if let Some(content_length) = content_length {
        let body_end = body_start.checked_add(content_length)?;
        while response.len() < body_end {
            let read = stream.read(&mut buffer).ok()?;
            if read == 0 {
                return None;
            }
            response.extend_from_slice(&buffer[..read]);
        }
        return Some((headers, response[body_start..body_end].to_vec()));
    }

    stream.read_to_end(&mut response).ok()?;
    Some((headers, response[body_start..].to_vec()))
}

fn opencodex_health_ok() -> bool {
    let address: SocketAddr = match "127.0.0.1:10100".parse() {
        Ok(address) => address,
        Err(_) => return false,
    };
    let timeout = Duration::from_millis(300);
    let mut stream = match TcpStream::connect_timeout(&address, timeout) {
        Ok(stream) => stream,
        Err(_) => return false,
    };
    if stream.set_read_timeout(Some(timeout)).is_err()
        || stream.set_write_timeout(Some(timeout)).is_err()
    {
        return false;
    }
    if stream
        .write_all(b"GET /healthz HTTP/1.1\r\nHost: 127.0.0.1:10100\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }

    let Some((headers, body)) = read_http_response(&mut stream) else {
        return false;
    };
    let status_ok = headers
        .lines()
        .next()
        .and_then(|line| line.split_whitespace().nth(1))
        == Some("200");
    if !status_ok {
        return false;
    }
    serde_json::from_slice::<Value>(&body)
        .ok()
        .and_then(|health| {
            health
                .get("status")
                .and_then(Value::as_str)
                .map(str::to_owned)
        })
        .as_deref()
        == Some("ok")
}

fn read_opencodex() -> Option<OpenCodexSnapshot> {
    if !opencodex_health_ok() {
        return None;
    }
    let directory = home_directory()?.join(".opencodex");
    let config = read_json_file(&directory.join("config.json"))?;
    let quota_cache = read_json_file(&directory.join("codex-quota-cache.json"))?;
    let active_account_id = config
        .get("activeCodexAccountId")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned);
    let default_provider = config
        .get("defaultProvider")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned);
    let mode = config
        .get("providers")
        .and_then(|providers| providers.get("openai"))
        .and_then(|openai| openai.get("codexAccountMode"))
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned);
    let default_model = config
        .get("defaultModel")
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            config
                .get("subagentModels")
                .and_then(Value::as_array)
                .and_then(|models| {
                    models
                        .iter()
                        .filter_map(Value::as_str)
                        .find(|value| !value.is_empty())
                })
                .map(str::to_owned)
        });
    let quotas = quota_cache.get("quotas")?.as_object()?;
    let mut accounts = quotas
        .iter()
        .filter_map(|(id, quota)| {
            let quota = quota.as_object()?;
            Some(OpenCodexAccount {
                id: id.to_owned(),
                weekly_percent: quota.get("weeklyPercent").and_then(as_number),
                short_percent: quota.get("shortPercent").and_then(as_number),
                weekly_reset_at: quota.get("weeklyResetAt").and_then(as_number),
                short_reset_at: quota.get("shortResetAt").and_then(as_number),
                active: active_account_id.as_deref() == Some(id),
            })
        })
        .collect::<Vec<_>>();
    accounts.sort_by(|left, right| left.id.cmp(&right.id));
    Some(OpenCodexSnapshot {
        default_provider,
        mode,
        default_model,
        accounts,
    })
}

fn codex_profile_directories() -> Vec<PathBuf> {
    let Some(home) = home_directory() else {
        return Vec::new();
    };
    let configured = env::var_os("CODEX_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| home.join(".codex"));
    let configured = if configured.is_absolute() {
        configured
    } else {
        env::current_dir()
            .map(|current| current.join(&configured))
            .unwrap_or(configured)
    };
    let configured_name = configured.file_name().and_then(|name| name.to_str());
    let is_standard_family = configured.parent() == Some(home.as_path())
        && configured_name.is_some_and(|name| name == ".codex" || name.starts_with(".codex-"));
    let mut candidates = if is_standard_family {
        let mut candidates = vec![home.join(".codex")];
        if let Ok(entries) = fs::read_dir(&home) {
            for entry in entries.flatten() {
                let path = entry.path();
                let name = path.file_name().and_then(|name| name.to_str());
                if name.is_some_and(|name| name.starts_with(".codex-")) {
                    candidates.push(path);
                }
            }
        }
        candidates
    } else {
        vec![configured]
    };
    candidates.retain(|path| path.is_dir());
    candidates
}

fn read_config_scalar(config: &str, key: &str) -> Option<String> {
    for line in config.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('[') {
            break;
        }
        let Some((found_key, raw_value)) = trimmed.split_once('=') else {
            continue;
        };
        if found_key.trim() != key {
            continue;
        }
        let value = raw_value.trim();
        return value
            .strip_prefix('"')
            .and_then(|value| value.strip_suffix('"'))
            .map(str::to_owned);
    }
    None
}

fn set_config_scalar(config: &str, key: &str, value: &str) -> String {
    let mut result = String::with_capacity(config.len() + key.len() + value.len() + 8);
    let mut before_section = true;
    let mut replaced = false;
    let newline = if config.contains("\r\n") {
        "\r\n"
    } else {
        "\n"
    };
    for line in config.split_inclusive(['\n']) {
        let content = line.strip_suffix('\n').unwrap_or(line);
        let (content, ending) = content
            .strip_suffix('\r')
            .map_or((content, ""), |content| (content, "\r"));
        if before_section && content.trim().starts_with('[') {
            before_section = false;
        }
        if before_section
            && !replaced
            && content
                .trim()
                .split_once('=')
                .is_some_and(|(found_key, _)| found_key.trim() == key)
        {
            result.push_str(key);
            result.push_str(" = \"");
            result.push_str(value);
            result.push_str("\"");
            result.push_str(ending);
            if line.ends_with('\n') {
                result.push('\n');
            }
            replaced = true;
        } else {
            result.push_str(line);
        }
    }
    if !replaced {
        let line = format!("{key} = \"{value}\"{newline}");
        result.insert_str(0, &line);
    }
    result
}

fn next_model(value: &str) -> &'static str {
    match value {
        "gpt-5.6-luna" => "gpt-5.6-terra",
        "gpt-5.6-terra" => "gpt-5.6-sol",
        "gpt-5.6-sol" => "gpt-5.6-luna",
        _ => "gpt-5.6-luna",
    }
}

fn next_effort(value: &str, model: &str) -> &'static str {
    match (value, model) {
        ("low", _) => "medium",
        ("medium", _) => "high",
        ("high", "gpt-5.6-sol") => "ultra",
        ("high", _) => "low",
        ("ultra", _) => "low",
        _ => "low",
    }
}

fn write_config_scalars(profile: String, model: Option<String>, effort: Option<String>) {
    thread::spawn(move || {
        let Some(home) = home_directory() else { return };
        let path = home.join(profile).join("config.toml");
        let Ok(mut config) = fs::read_to_string(&path) else {
            return;
        };
        if let Some(model) = model.as_deref() {
            config = set_config_scalar(&config, "model", model);
        }
        if let Some(effort) = effort.as_deref() {
            config = set_config_scalar(&config, "model_reasoning_effort", effort);
        }
        let temporary = path.with_extension(format!("toml.{}.tmp", std::process::id()));
        // Use arquivo temporario para nao truncar o config em caso de crash.
        if fs::write(&temporary, config).is_ok() {
            let _ = fs::rename(&temporary, &path);
        }
    });
}

fn newest_rollout_mtime(directory: &Path) -> Option<SystemTime> {
    let mut newest = None;
    let entries = fs::read_dir(directory).ok()?;
    for entry in entries.flatten() {
        let path = entry.path();
        let metadata = match entry.metadata() {
            Ok(metadata) => metadata,
            Err(_) => continue,
        };
        if metadata.is_dir() {
            if let Some(mtime) = newest_rollout_mtime(&path) {
                if newest.map_or(true, |known| mtime > known) {
                    newest = Some(mtime);
                }
            }
        } else if path
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.starts_with("rollout-") && name.ends_with(".jsonl"))
        {
            if let Ok(mtime) = metadata.modified() {
                if newest.map_or(true, |known| mtime > known) {
                    newest = Some(mtime);
                }
            }
        }
    }
    newest
}

fn read_latest_codex_profile() -> Option<String> {
    let mut latest = None;
    for directory in codex_profile_directories() {
        let Some(mtime) = newest_rollout_mtime(&directory.join("sessions")) else {
            continue;
        };
        let Some(name) = directory.file_name().and_then(|name| name.to_str()) else {
            continue;
        };
        if latest
            .as_ref()
            .map_or(true, |(known, _): &(SystemTime, String)| mtime > *known)
        {
            latest = Some((mtime, name.to_owned()));
        }
    }
    latest.map(|(_, profile)| profile)
}

fn process_output_message(output: &std::process::Output) -> String {
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_owned();
    if stderr.is_empty() {
        "sem detalhes".to_owned()
    } else {
        stderr
    }
}

fn load_poke_mode() -> bool {
    claude_config_dir()
        .and_then(|directory| fs::read_to_string(directory.join("painel-config.json")).ok())
        .and_then(|contents| serde_json::from_str::<Value>(&contents).ok())
        .and_then(|value| value.get("modo_poke").and_then(Value::as_bool))
        .unwrap_or(true)
}

fn save_poke_mode(poke_mode: bool) {
    let Some(directory) = claude_config_dir() else {
        return;
    };
    if fs::create_dir_all(&directory).is_ok() {
        let contents = format!("{{\"modo_poke\":{poke_mode}}}\n");
        let _ = fs::write(directory.join("painel-config.json"), contents);
    }
}

fn find_repo() -> PathBuf {
    let mut starts = Vec::new();
    if let Ok(current) = env::current_dir() {
        starts.push(current);
    }
    if let Ok(executable) = env::current_exe() {
        if let Some(parent) = executable.parent() {
            starts.push(parent.to_path_buf());
        }
    }

    for start in starts {
        let mut candidate = Some(start.as_path());
        while let Some(directory) = candidate {
            if directory
                .join("skills")
                .join("codex")
                .join("status.py")
                .is_file()
            {
                return directory.to_path_buf();
            }
            candidate = directory.parent();
        }
    }
    PathBuf::from(".")
}

fn show_command_center(
    ui: &mut egui::Ui,
    content_width: f32,
    status: Option<&Result<Value, String>>,
    cswap: Option<&Result<Value, String>>,
    active_profile: Option<&str>,
    profile_settings: &[(String, Option<String>, Option<String>)],
    pokemon: &mut PokemonCache,
    context: &egui::Context,
    poke_mode: bool,
) {
    section_frame(
        ui,
        "AGORA // CENTRO DE COMANDO",
        content_width,
        |ui, content_width| {
            let stacked = content_width < arena_wide_min_width(poke_mode);
            let (arena, _) = ui.allocate_exact_size(
                egui::vec2(content_width, arena_height(poke_mode, stacked)),
                egui::Sense::hover(),
            );
            let painter = ui.painter().with_clip_rect(arena);

            let claude_account = cswap
                .and_then(|result| result.as_ref().ok())
                .and_then(|value| value.get("accounts"))
                .and_then(Value::as_array)
                .and_then(|accounts| {
                    accounts.iter().find(|account| {
                        account.get("active").and_then(Value::as_bool) == Some(true)
                    })
                });
            let claude_email = claude_account
                .and_then(|account| account.get("email"))
                .and_then(Value::as_str)
                .unwrap_or("conta não identificada");
            let claude_hp = claude_account
                .and_then(|account| usage_percent(account, "fiveHour"))
                .map(remaining_hp);
            let claude_texture = poke_mode
                .then(|| pokemon.texture(context, "claude", claude_email, true))
                .flatten()
                .map(|texture| texture.id());

            let codex_account = status
                .and_then(|result| result.as_ref().ok())
                .and_then(|value| value.get("codex"))
                .and_then(|codex| codex.get("contas"))
                .and_then(Value::as_array)
                .and_then(|accounts| {
                    active_profile.and_then(|profile| {
                        accounts.iter().find(|account| {
                            account.get("perfil").and_then(Value::as_str) == Some(profile)
                        })
                    })
                });
            let codex_email = codex_account
                .and_then(|account| account.get("email"))
                .and_then(Value::as_str)
                .unwrap_or("conta não identificada");
            let codex_texture = poke_mode
                .then(|| pokemon.texture(context, "codex", codex_email, false))
                .flatten()
                .map(|texture| texture.id());
            let codex_model = codex_account
                .and_then(|account| account.get("perfil"))
                .and_then(Value::as_str)
                .and_then(|profile| {
                    profile_settings
                        .iter()
                        .find(|(known, _, _)| known == profile)
                })
                .and_then(|(_, model, _)| model.as_deref())
                .unwrap_or("—");
            let codex_effort = codex_account
                .and_then(|account| account.get("perfil"))
                .and_then(Value::as_str)
                .and_then(|profile| {
                    profile_settings
                        .iter()
                        .find(|(known, _, _)| known == profile)
                })
                .and_then(|(_, _, effort)| effort.as_deref())
                .unwrap_or("—");
            let codex_hp = codex_account
                .and_then(|account| quota_percent_at(account, 0))
                .map(remaining_hp);

            if stacked {
                let row_height = arena_side_height(poke_mode);
                draw_arena_side(
                    &painter,
                    ui,
                    egui::Rect::from_min_size(
                        egui::pos2(arena.left(), arena.top()),
                        egui::vec2(arena.width(), row_height),
                    ),
                    &format!(
                        "CLAUDE · {}",
                        claude_email.split('@').next().unwrap_or(claude_email)
                    ),
                    claude_hp,
                    claude_texture,
                    true,
                    poke_mode,
                    ArenaSideLayout::Stacked,
                );
                draw_arena_delegation(
                    &painter,
                    ui,
                    egui::Rect::from_min_size(
                        egui::pos2(arena.left(), arena.top() + row_height),
                        egui::vec2(arena.width(), 38.0),
                    ),
                    status,
                );
                draw_arena_side(
                    &painter,
                    ui,
                    egui::Rect::from_min_size(
                        egui::pos2(arena.left(), arena.bottom() - row_height),
                        egui::vec2(arena.width(), row_height),
                    ),
                    &format!("CODEX · {codex_model}/{codex_effort}"),
                    codex_hp,
                    codex_texture,
                    false,
                    poke_mode,
                    ArenaSideLayout::Stacked,
                );
            } else if poke_mode {
                let row_height = ARENA_SPRITE_SIZE;
                draw_arena_side(
                    &painter,
                    ui,
                    egui::Rect::from_min_size(
                        egui::pos2(arena.left(), arena.top()),
                        egui::vec2(arena.width(), row_height),
                    ),
                    &format!("CODEX · {codex_model}/{codex_effort}"),
                    codex_hp,
                    codex_texture,
                    false,
                    true,
                    ArenaSideLayout::WideTop,
                );
                draw_arena_delegation(
                    &painter,
                    ui,
                    egui::Rect::from_min_size(
                        egui::pos2(arena.left(), arena.top() + row_height),
                        egui::vec2(arena.width(), ARENA_DELEGATION_HEIGHT),
                    ),
                    status,
                );
                draw_arena_side(
                    &painter,
                    ui,
                    egui::Rect::from_min_size(
                        egui::pos2(arena.left(), arena.bottom() - ARENA_SPRITE_SIZE),
                        egui::vec2(arena.width(), row_height),
                    ),
                    &format!(
                        "CLAUDE · {}",
                        claude_email.split('@').next().unwrap_or(claude_email)
                    ),
                    claude_hp,
                    claude_texture,
                    true,
                    true,
                    ArenaSideLayout::WideBottom,
                );
            } else {
                let side_width =
                    (arena.width() - ARENA_MIN_DELEGATION_WIDTH - ARENA_COLUMN_GAP * 2.0) / 2.0;
                let claude_rect = egui::Rect::from_min_size(
                    arena.left_top(),
                    egui::vec2(side_width, arena.height()),
                );
                let codex_rect = egui::Rect::from_min_size(
                    egui::pos2(arena.right() - side_width, arena.top()),
                    egui::vec2(side_width, arena.height()),
                );
                let delegation_rect = egui::Rect::from_min_size(
                    egui::pos2(claude_rect.right() + ARENA_COLUMN_GAP, arena.top()),
                    egui::vec2(
                        (codex_rect.left() - claude_rect.right() - ARENA_COLUMN_GAP * 2.0).max(0.0),
                        arena.height(),
                    ),
                );
                draw_arena_side(
                    &painter,
                    ui,
                    claude_rect,
                    &format!(
                        "CLAUDE · {}",
                        claude_email.split('@').next().unwrap_or(claude_email)
                    ),
                    claude_hp,
                    claude_texture,
                    true,
                    false,
                    ArenaSideLayout::Sober,
                );
                draw_arena_delegation(&painter, ui, delegation_rect, status);
                draw_arena_side(
                    &painter,
                    ui,
                    codex_rect,
                    &format!("CODEX · {codex_model}/{codex_effort}"),
                    codex_hp,
                    codex_texture,
                    false,
                    false,
                    ArenaSideLayout::Sober,
                );
            }
        },
    );
}

#[derive(Clone, Copy)]
enum ArenaSideLayout {
    WideTop,
    WideBottom,
    Stacked,
    Sober,
}

fn arena_side_height(poke_mode: bool) -> f32 {
    if poke_mode {
        ARENA_SPRITE_SIZE
    } else {
        ARENA_PLATE_HEIGHT
    }
}

fn arena_height(poke_mode: bool, stacked: bool) -> f32 {
    if stacked {
        arena_side_height(poke_mode) * 2.0 + ARENA_DELEGATION_HEIGHT
    } else if poke_mode {
        ARENA_SPRITE_SIZE * 2.0 + ARENA_DELEGATION_HEIGHT
    } else {
        ARENA_PLATE_HEIGHT
    }
}

fn arena_wide_min_width(poke_mode: bool) -> f32 {
    let side_width = if poke_mode {
        ARENA_MIN_SIDE_WIDTH
    } else {
        ARENA_MIN_PLATE_WIDTH
    };
    side_width * 2.0 + ARENA_MIN_DELEGATION_WIDTH + ARENA_COLUMN_GAP * 2.0
}

fn remaining_hp(used: f64) -> f64 {
    100.0 - normalized_percent(used)
}

fn draw_arena_side(
    painter: &egui::Painter,
    ui: &mut egui::Ui,
    rect: egui::Rect,
    plate_text: &str,
    hp: Option<f64>,
    texture: Option<egui::TextureId>,
    player: bool,
    poke_mode: bool,
    layout: ArenaSideLayout,
) {
    let sprite_size = ARENA_SPRITE_SIZE;
    let sprite_zone = if player {
        sprite_size + 46.0
    } else {
        sprite_size
    };
    let plate_width = match layout {
        ArenaSideLayout::Sober => (rect.width() - 8.0).max(0.0),
        _ if poke_mode => (rect.width() - sprite_zone - ARENA_COLUMN_GAP)
            .min(ARENA_PLATE_MAX_WIDTH)
            .max(96.0),
        _ => (rect.width() - 8.0).min(ARENA_PLATE_MAX_WIDTH).max(96.0),
    }
    .min(rect.width());
    let plate_left = match layout {
        ArenaSideLayout::WideTop | ArenaSideLayout::Sober => rect.left() + 4.0,
        ArenaSideLayout::WideBottom | ArenaSideLayout::Stacked => rect.right() - plate_width - 4.0,
    };
    let plate_top = match layout {
        ArenaSideLayout::WideTop => rect.top(),
        ArenaSideLayout::WideBottom => rect.bottom() - ARENA_PLATE_HEIGHT,
        ArenaSideLayout::Stacked | ArenaSideLayout::Sober => {
            rect.center().y - ARENA_PLATE_HEIGHT / 2.0
        }
    };
    let plate = egui::Rect::from_min_size(
        egui::pos2(plate_left, plate_top),
        egui::vec2(plate_width, ARENA_PLATE_HEIGHT),
    );
    painter.rect_filled(plate, 0.0, ground());
    painter.rect_stroke(plate, 0.0, egui::Stroke::new(1.0_f32, border()));
    draw_arena_text(
        painter,
        plate.left_top() + egui::vec2(8.0, 6.0),
        plate_text,
        (plate.width() - 16.0).max(0.0),
        egui::FontId::monospace(11.0),
        text_color(),
    );
    draw_arena_hp(ui, painter, plate, hp);

    let sprite_left = match layout {
        ArenaSideLayout::WideTop => rect.right() - sprite_size,
        ArenaSideLayout::WideBottom | ArenaSideLayout::Stacked => {
            rect.left() + if player { 46.0 } else { 0.0 }
        }
        ArenaSideLayout::Sober => rect.left(),
    };
    if !poke_mode || matches!(layout, ArenaSideLayout::Sober) {
        return;
    }
    let sprite_top = match layout {
        ArenaSideLayout::WideTop => rect.top(),
        ArenaSideLayout::WideBottom => rect.bottom() - sprite_size,
        ArenaSideLayout::Stacked => rect.center().y - sprite_size / 2.0,
        ArenaSideLayout::Sober => rect.top(),
    };
    let sprite_rect = egui::Rect::from_min_size(
        egui::pos2(sprite_left, sprite_top),
        egui::vec2(sprite_size, sprite_size),
    );
    if player {
        draw_trainer(
            painter,
            egui::pos2(rect.left() + 5.0, sprite_rect.top() + 12.0),
        );
    }
    if let Some(texture) = texture {
        painter.image(
            texture,
            sprite_rect,
            egui::Rect::from_min_max(egui::pos2(0.0, 0.0), egui::pos2(1.0, 1.0)),
            egui::Color32::WHITE,
        );
    }
}

fn draw_arena_hp(ui: &mut egui::Ui, _painter: &egui::Painter, plate: egui::Rect, hp: Option<f64>) {
    let bar_rect = egui::Rect::from_min_size(
        egui::pos2(plate.left() + 8.0, plate.bottom() - 17.0),
        egui::vec2((plate.width() - 16.0).max(0.0), 11.0),
    );
    ui.allocate_ui_at_rect(bar_rect, |ui| {
        let (blocks, _) = allocate_blocks(ui, bar_rect.width());
        paint_blocks(
            ui,
            blocks,
            |index| hp.is_some_and(|value| index < ((value / 10.0).round() as usize).min(10)),
            |_| hp.map(hp_color).unwrap_or(dim_color()),
            |_| egui::Color32::from_rgba_unmultiplied(0x4A, 0x5D, 0x3F, 80),
        );
    });
}

fn draw_arena_delegation(
    painter: &egui::Painter,
    ui: &mut egui::Ui,
    rect: egui::Rect,
    status: Option<&Result<Value, String>>,
) {
    let job = status
        .and_then(|result| result.as_ref().ok())
        .and_then(|value| value.get("jobs"))
        .and_then(Value::as_array)
        .and_then(|jobs| jobs.first());
    let text = job.map(|job| {
        format!(
            "{} · {}",
            string_field(job, "projeto"),
            format_duration(number_field(job, "segundos"))
        )
    });
    let text = text.as_deref().unwrap_or("aguardando delegação");
    let text_color = if job.is_some() {
        text_color()
    } else {
        dim_color()
    };
    draw_arena_text_centered(
        painter,
        rect.center().x,
        rect.top() + 4.0,
        text,
        (rect.width() - 12.0).max(0.0),
        egui::FontId::monospace(11.0),
        text_color,
    );
    if job.is_some() {
        let blocks = egui::Rect::from_center_size(
            rect.center() + egui::vec2(0.0, 9.0),
            egui::vec2(84.0, 12.0),
        );
        ui.allocate_ui_at_rect(blocks, loading_blocks);
    }
}

fn draw_arena_text(
    painter: &egui::Painter,
    position: egui::Pos2,
    text: &str,
    max_width: f32,
    font: egui::FontId,
    color: egui::Color32,
) {
    let text = truncate_arena_text(painter, text, max_width, font.clone(), color);
    painter.galley(position, painter.layout_no_wrap(text, font, color));
}

fn draw_arena_text_centered(
    painter: &egui::Painter,
    center_x: f32,
    top: f32,
    text: &str,
    max_width: f32,
    font: egui::FontId,
    color: egui::Color32,
) {
    let text = truncate_arena_text(painter, text, max_width, font.clone(), color);
    let galley = painter.layout_no_wrap(text, font, color);
    painter.galley(egui::pos2(center_x - galley.size().x / 2.0, top), galley);
}

fn truncate_arena_text(
    painter: &egui::Painter,
    text: &str,
    max_width: f32,
    font: egui::FontId,
    color: egui::Color32,
) -> String {
    if max_width <= 0.0 {
        return String::new();
    }
    if painter
        .layout_no_wrap(text.to_owned(), font.clone(), color)
        .size()
        .x
        <= max_width
    {
        return text.to_owned();
    }

    let ellipsis = "…";
    let ellipsis_width = painter
        .layout_no_wrap(ellipsis.to_owned(), font.clone(), color)
        .size()
        .x;
    if ellipsis_width > max_width {
        return String::new();
    }

    let mut prefix = String::new();
    for character in text.chars() {
        let candidate = format!("{prefix}{character}{ellipsis}");
        if painter
            .layout_no_wrap(candidate, font.clone(), color)
            .size()
            .x
            <= max_width
        {
            prefix.push(character);
        } else {
            break;
        }
    }
    format!("{prefix}{ellipsis}")
}

fn draw_trainer(painter: &egui::Painter, origin: egui::Pos2) {
    let block = 3.0;
    let rect = |x: f32, y: f32, width: f32, height: f32| {
        egui::Rect::from_min_size(
            origin + egui::vec2(x * block, y * block),
            egui::vec2(width * block, height * block),
        )
    };
    painter.rect_filled(rect(4.0, 0.0, 4.0, 4.0), 0.0, text_color());
    painter.rect_filled(rect(3.0, 4.0, 6.0, 6.0), 0.0, border());
    painter.rect_filled(rect(1.0, 5.0, 2.0, 4.0), 0.0, text_color());
    painter.rect_filled(rect(9.0, 5.0, 2.0, 4.0), 0.0, text_color());
    painter.rect_filled(rect(3.0, 10.0, 2.0, 6.0), 0.0, border());
    painter.rect_filled(rect(7.0, 10.0, 2.0, 6.0), 0.0, border());
}

fn show_command_line(ui: &mut egui::Ui, content_width: f32, cswap: Option<&Result<Value, String>>) {
    section_frame(ui, "NO COMANDO", content_width, |ui, content_width| {
        let value = match cswap {
            None => {
                dim_label(ui, content_width, "carregando…");
                return;
            }
            Some(Err(error)) => {
                error_label(ui, content_width, error);
                return;
            }
            Some(Ok(value)) => value,
        };
        let account = value
            .get("accounts")
            .and_then(Value::as_array)
            .and_then(|accounts| {
                accounts.iter().find_map(|account| {
                    let active = account
                        .get("active")
                        .and_then(Value::as_bool)
                        .unwrap_or(false);
                    active
                        .then(|| account.get("email").and_then(Value::as_str))
                        .flatten()
                        .filter(|email| !email.is_empty())
                })
            })
            .unwrap_or("conta não identificada");
        data_label(
            ui,
            content_width,
            format!("Claude Code · {account} · modelo não disponível"),
        );
    });
}

fn show_running_jobs(
    ui: &mut egui::Ui,
    content_width: f32,
    status: Option<&Result<Value, String>>,
) {
    section_frame(ui, "RODANDO AGORA", content_width, |ui, content_width| {
        let value = match status {
            None => {
                dim_label(ui, content_width, "carregando…");
                return;
            }
            Some(Err(error)) => {
                error_label(ui, content_width, error);
                return;
            }
            Some(Ok(value)) => value,
        };

        let jobs = value.get("jobs").and_then(Value::as_array);
        if jobs.is_none_or(Vec::is_empty) {
            dim_label(ui, content_width, "nenhuma delegação em andamento");
            return;
        }
        if let Some(jobs) = jobs {
            let gaps = ui.spacing().item_spacing.x * 3.0;
            let widths = column_widths(
                content_width - 84.0 - gaps,
                gaps,
                &[(70.0, 100.0), (130.0, 320.0), (44.0, 60.0)],
            );
            for (index, job) in jobs.iter().enumerate() {
                if let Some(widths) = widths.as_ref() {
                    ui.horizontal(|ui| {
                        ui.add_sized(
                            [widths[0], 20.0],
                            egui::Label::new(data_text(string_field(job, "modelo"))).truncate(true),
                        );
                        ui.add_sized(
                            [widths[1], 20.0],
                            egui::Label::new(data_text(string_field(job, "projeto")))
                                .truncate(true),
                        );
                        ui.add_sized(
                            [widths[2], 20.0],
                            egui::Label::new(data_text(format_duration(number_field(
                                job, "segundos",
                            )))),
                        );
                        loading_blocks(ui);
                    });
                } else {
                    ui.horizontal(|ui| {
                        ui.label(data_text(string_field(job, "modelo")));
                        ui.add_space(8.0);
                        ui.label(data_text(format_duration(number_field(job, "segundos"))));
                    });
                    ui.scope(|ui| {
                        ui.set_max_width(content_width);
                        ui.add(
                            egui::Label::new(data_text(string_field(job, "projeto"))).wrap(true),
                        );
                    });
                    loading_blocks(ui);
                }
                if index + 1 < jobs.len() {
                    stacked_divider(ui);
                }
            }
        }
    });
}

fn show_claude(
    ui: &mut egui::Ui,
    content_width: f32,
    cswap: Option<&Result<Value, String>>,
    fixacao: Option<&Value>,
    pokemon: &mut PokemonCache,
    context: &egui::Context,
    poke_mode: bool,
) {
    section_frame(ui, "CLAUDE", content_width, |ui, content_width| {
        let value = match cswap {
            None => {
                dim_label(ui, content_width, "carregando…");
                return;
            }
            Some(Err(error)) => {
                error_label(ui, content_width, error);
                return;
            }
            Some(Ok(value)) => value,
        };
        let accounts = match value.get("accounts").and_then(Value::as_array) {
            Some(accounts) => accounts,
            None => {
                error_label(ui, content_width, "cswap: JSON sem accounts[]");
                return;
            }
        };
        let accounts_for_fixation = accounts
            .iter()
            .filter_map(|account| {
                Some(ClaudeAccountTarget {
                    number: account.get("number").and_then(Value::as_i64)?,
                    email: account.get("email").and_then(Value::as_str)?.to_owned(),
                    disabled: account
                        .get("disabled")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                })
            })
            .collect::<Vec<_>>();

        let cols = [
            (MIN_CLAUDE_ACCOUNT_WIDTH, CLAUDE_ACCOUNT_WIDTH),
            (MIN_CLAUDE_QUOTA_WIDTH, CLAUDE_QUOTA_WIDTH),
            (MIN_CLAUDE_QUOTA_WIDTH, CLAUDE_QUOTA_WIDTH),
        ];
        match column_widths(content_width, CLAUDE_GRID_GAPS, &cols) {
            Some(widths) => {
                egui::Grid::new("claude_accounts")
                    .num_columns(3)
                    .min_col_width(0.0)
                    .min_row_height(20.0)
                    .spacing(egui::vec2(10.0, 8.0))
                    .show(ui, |ui| {
                ui.add_sized(
                    [widths[0], 20.0],
                    egui::Label::new(pixel_text("conta", 10.0)),
                );
                ui.add_sized(
                    [widths[1], 20.0],
                    egui::Label::new(pixel_text("5h", 10.0)),
                );
                ui.add_sized(
                    [widths[2], 20.0],
                    egui::Label::new(pixel_text("7d", 10.0)),
                );
                ui.end_row();

                for account in accounts {
                    let five_hour = usage_percent(account, "fiveHour");
                    let seven_day = usage_percent(account, "sevenDay");
                    let cache_age = usage_age(account);
                    let five_hour_countdown = usage_text(account, "fiveHour", "countdown");
                    let seven_day_countdown = usage_text(account, "sevenDay", "countdown");
                    let five_hour_clock = usage_clock(account, "fiveHour");
                    let seven_day_clock = usage_clock(account, "sevenDay");
                    let active = account
                        .get("active")
                        .and_then(Value::as_bool)
                        .unwrap_or(false);
                    let disabled = account
                        .get("disabled")
                        .and_then(Value::as_bool)
                        .unwrap_or(false);
                    let number = account.get("number").and_then(Value::as_i64);
                    let precisa_relogin = account
                        .get("usageStatus")
                        .and_then(Value::as_str)
                        .is_some_and(|status| status == "relogin_required");
                    let email = account
                        .get("email")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_owned();
                    let fixada = email_esta_fixado(fixacao, &email);
                    let em_espera = disabled && fixacao_tem_numero(fixacao, number);
                    let fainted = [five_hour, seven_day]
                        .into_iter()
                        .flatten()
                        .any(|percent| percent >= 100.0);
                    // O sprite e a identidade da conta, nao indicador de cota: conta em
                    // relogin_required tem usage=null e antes perdia o bichinho junto com o
                    // numero. A secao CODEX abaixo sempre fez assim.
                    let texture = poke_mode
                        .then(|| pokemon.texture(context, "claude", &email, false).cloned())
                        .flatten();
                    let species = poke_mode
                        .then(|| pokemon.info("claude", &email).map(|info| info.name))
                        .flatten();
                    ui.allocate_ui_with_layout(
                        egui::vec2(widths[0], 40.0),
                        egui::Layout::left_to_right(egui::Align::Center),
                        |ui| {
                            if let Some(texture) = texture.as_ref() {
                                ui.add(
                                        egui::Image::new((texture.id(), egui::vec2(40.0, 40.0)))
                                            .fit_to_exact_size(egui::vec2(40.0, 40.0))
                                            .texture_options(egui::TextureOptions::NEAREST)
                                            .tint(if fainted {
                                                dim_color()
                                            } else {
                                                egui::Color32::WHITE
                                            }),
                                );
                            } else {
                                // Sem sprite o espaco continua reservado: a largura ja desconta
                                // ACCOUNT_SPRITE_SIZE, e sem isto o email truncava e a linha
                                // deslizava para a esquerda em relacao as outras.
                                ui.add_space(ACCOUNT_SPRITE_SIZE);
                            }
                            let cursor = if active { "●" } else { " " };
                            let cursor_response = ui.add_sized(
                                [ACCOUNT_CURSOR_WIDTH, 20.0],
                                egui::Label::new(
                                    egui::RichText::new(cursor)
                                        .color(hp_ok())
                                        .font(egui::FontId::monospace(13.0)),
                                ),
                            );
                            if active {
                                cursor_response.on_hover_text("conta no comando");
                            }
                            let info_width = (widths[0]
                                - ACCOUNT_SPRITE_SIZE
                                - ACCOUNT_CURSOR_WIDTH
                                - ui.spacing().item_spacing.x * 2.0)
                                .max(0.0);
                            ui.scope(|ui| {
                                ui.spacing_mut().item_spacing.y = 0.0;
                                ui.vertical(|ui| {
                                    ui.add_sized(
                                        [info_width, 20.0],
                                        egui::Label::new(data_text(&email)).truncate(true),
                                    )
                                    .on_hover_text(string_field(account, "usageStatus"));
                                    ui.horizontal(|ui| {
                                        // relogin_required so existia como tooltip do email:
                                        // a conta aparecia sem numero e sem motivo visivel.
                                        let marca = if precisa_relogin {
                                            Some("relogar")
                                        } else if em_espera {
                                            Some("em espera")
                                        } else if disabled {
                                            Some("desabilitada")
                                        } else {
                                            None
                                        };
                                        let marca_width = marca
                                            .map(|texto| {
                                                text_width(texto) + ui.spacing().item_spacing.x
                                            })
                                            .unwrap_or(0.0);
                                        let desmaiou_width = if fainted {
                                            text_width("desmaiou") + ui.spacing().item_spacing.x
                                        } else {
                                            0.0
                                        };
                                        let can_use = !active
                                            && number.is_some()
                                            && (!disabled || em_espera);
                                        let usar_width = if can_use {
                                            text_width("usar") + ui.spacing().item_spacing.x
                                        } else {
                                            0.0
                                        };
                                        let ativa_width = if active {
                                            text_width("ativa") + ui.spacing().item_spacing.x
                                        } else {
                                            0.0
                                        };
                                        let fixada_width = if fixada {
                                            text_width("fixada") + ui.spacing().item_spacing.x
                                        } else {
                                            0.0
                                        };
                                        if let Some(species) = species.as_deref() {
                                            ui.add_sized(
                                                [
                                                    (info_width
                                                        - marca_width
                                                        - desmaiou_width
                                                        - ativa_width
                                                        - fixada_width
                                                        - usar_width)
                                                        .max(0.0),
                                                    20.0,
                                                ],
                                                egui::Label::new(pixel_text(species, 10.0))
                                                    .truncate(true),
                                            );
                                        }
                                        if let Some(texto) = marca {
                                            ui.add_sized(
                                                [text_width(texto), 20.0],
                                                egui::Label::new(dim_text(texto)).truncate(true),
                                            );
                                        }
                                        if fainted {
                                            ui.add_sized(
                                                [text_width("desmaiou"), 20.0],
                                                egui::Label::new(dim_text("desmaiou")).truncate(true),
                                            );
                                        }
                                        if active {
                                            ui.add_sized(
                                                [text_width("ativa"), 20.0],
                                                egui::Label::new(dim_text("ativa")).truncate(true),
                                            );
                                        }
                                        if fixada {
                                            ui.add_sized(
                                                [text_width("fixada"), 20.0],
                                                egui::Label::new(dim_text("fixada")).truncate(true),
                                            );
                                        }
                                        if can_use {
                                            if let Some(number) = number {
                                                if ui
                                                    .add(egui::Button::new(pixel_text("usar", 9.0)))
                                                    .on_hover_cursor(egui::CursorIcon::PointingHand)
                                                    .on_hover_text(
                                                        "fixar esta conta até 100% · as outras ficam em espera",
                                                    )
                                                    .clicked()
                                                {
                                                    let email = email.clone();
                                                    let accounts = accounts_for_fixation.clone();
                                                    thread::spawn(move || {
                                                        fix_claude_account(number, email, accounts);
                                                    });
                                                 }
                                            }
                                        }
                                    });
                                });
                            });
                        },
                    );
                    ui.allocate_ui_with_layout(
                        egui::vec2(widths[1], 20.0),
                        egui::Layout::left_to_right(egui::Align::Center),
                        |ui| {
                            usage_bar_aged(
                                ui,
                                five_hour,
                                five_hour_countdown,
                                five_hour_clock,
                                cache_age.as_deref(),
                            )
                        },
                    );
                    ui.allocate_ui_with_layout(
                        egui::vec2(widths[2], 20.0),
                        egui::Layout::left_to_right(egui::Align::Center),
                        |ui| {
                            usage_bar_aged(
                                ui,
                                seven_day,
                                seven_day_countdown,
                                seven_day_clock,
                                cache_age.as_deref(),
                            )
                        },
                    );
                    ui.end_row();
                }
                    });
            }
            None => {
                for (index, account) in accounts.iter().enumerate() {
                    render_claude_account_stacked(
                        ui,
                        account,
                        content_width,
                        pokemon,
                        context,
                        poke_mode,
                        fixacao,
                        accounts_for_fixation.clone(),
                    );
                    if index + 1 < accounts.len() {
                        stacked_divider(ui);
                    }
                }
            }
        }
    });
}

/// Reparte `available` entre colunas descritas como (minimo, ideal).
/// None = nao cabe nem no minimo -> o chamador empilha em blocos.
fn column_widths(available: f32, gaps: f32, cols: &[(f32, f32)]) -> Option<Vec<f32>> {
    let min: f32 = cols.iter().map(|(m, _)| m).sum();
    let ideal: f32 = cols.iter().map(|(_, i)| i).sum();
    if available < min + gaps {
        return None;
    }
    let scale = ((available - gaps - min) / (ideal - min)).clamp(0.0, 1.0);
    Some(cols.iter().map(|(m, i)| m + (i - m) * scale).collect())
}

fn stacked_divider(ui: &mut egui::Ui) {
    ui.add_space(8.0);
    let (rect, _) =
        ui.allocate_exact_size(egui::vec2(ui.available_width(), 1.0), egui::Sense::hover());
    ui.painter().line_segment(
        [rect.left_center(), rect.right_center()],
        egui::Stroke::new(1.0_f32, border()),
    );
}

fn render_claude_account_stacked(
    ui: &mut egui::Ui,
    account: &Value,
    content_width: f32,
    pokemon: &mut PokemonCache,
    context: &egui::Context,
    poke_mode: bool,
    fixacao: Option<&Value>,
    accounts_for_fixation: Vec<ClaudeAccountTarget>,
) {
    let five_hour = usage_percent(account, "fiveHour");
    let seven_day = usage_percent(account, "sevenDay");
    let cache_age = usage_age(account);
    let five_hour_countdown = usage_text(account, "fiveHour", "countdown");
    let seven_day_countdown = usage_text(account, "sevenDay", "countdown");
    let five_hour_clock = usage_clock(account, "fiveHour");
    let seven_day_clock = usage_clock(account, "sevenDay");
    let active = account
        .get("active")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let disabled = account
        .get("disabled")
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let number = account.get("number").and_then(Value::as_i64);
    let precisa_relogin = account
        .get("usageStatus")
        .and_then(Value::as_str)
        .is_some_and(|status| status == "relogin_required");
    let email = account
        .get("email")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_owned();
    let fixada = email_esta_fixado(fixacao, &email);
    let em_espera = disabled && fixacao_tem_numero(fixacao, number);
    let fainted = [five_hour, seven_day]
        .into_iter()
        .flatten()
        .any(|percent| percent >= 100.0);
    let texture = poke_mode
        .then(|| pokemon.texture(context, "claude", &email, false).cloned())
        .flatten();
    let species = poke_mode
        .then(|| pokemon.info("claude", &email).map(|info| info.name))
        .flatten();
    let width = content_width;

    ui.horizontal(|ui| {
        if poke_mode {
            if let Some(texture) = texture.as_ref() {
                ui.add(
                    egui::Image::new((texture.id(), egui::vec2(40.0, 40.0)))
                        .fit_to_exact_size(egui::vec2(40.0, 40.0))
                        .texture_options(egui::TextureOptions::NEAREST)
                        .tint(if fainted {
                            dim_color()
                        } else {
                            egui::Color32::WHITE
                        }),
                );
            } else {
                ui.add_space(ACCOUNT_SPRITE_SIZE);
            }
        }
        let cursor = if active { "●" } else { " " };
        let cursor_response = ui.add_sized(
            [ACCOUNT_CURSOR_WIDTH, 20.0],
            egui::Label::new(
                egui::RichText::new(cursor)
                    .color(hp_ok())
                    .font(egui::FontId::monospace(13.0)),
            ),
        );
        if active {
            cursor_response.on_hover_text("conta no comando");
        }
        let reserved = if poke_mode { ACCOUNT_SPRITE_SIZE } else { 0.0 }
            + ACCOUNT_CURSOR_WIDTH
            + ui.spacing().item_spacing.x * 2.0;
        ui.scope(|ui| {
            ui.set_max_width((width - reserved).max(0.0));
            ui.add(egui::Label::new(data_text(&email)).wrap(true))
                .on_hover_text(string_field(account, "usageStatus"));
        });
    });

    ui.horizontal_wrapped(|ui| {
        if let Some(species) = species.as_deref() {
            ui.label(pixel_text(species, 10.0));
        }
        if fainted {
            ui.label(dim_text("desmaiou"));
        }
        if precisa_relogin {
            ui.label(dim_text("relogar"));
        } else if em_espera {
            ui.label(dim_text("em espera"));
        } else if disabled {
            ui.label(dim_text("desabilitada"));
        }
        if active {
            ui.label(dim_text("ativa"));
        }
        if fixada {
            ui.label(dim_text("fixada"));
        }
        if !active && number.is_some() && (!disabled || em_espera) {
            if let Some(number) = number {
                if ui
                    .add(egui::Button::new(pixel_text("usar", 9.0)))
                    .on_hover_cursor(egui::CursorIcon::PointingHand)
                    .on_hover_text("fixar esta conta até 100% · as outras ficam em espera")
                    .clicked()
                {
                    let email = email.clone();
                    let accounts = accounts_for_fixation.clone();
                    thread::spawn(move || fix_claude_account(number, email, accounts));
                }
            }
        }
    });

    ui.horizontal(|ui| {
        ui.add_sized([34.0, 20.0], egui::Label::new(pixel_text("5h", 10.0)));
        usage_bar_aged(
            ui,
            five_hour,
            five_hour_countdown,
            five_hour_clock,
            cache_age.as_deref(),
        );
    });
    ui.horizontal(|ui| {
        ui.add_sized([34.0, 20.0], egui::Label::new(pixel_text("7d", 10.0)));
        usage_bar_aged(
            ui,
            seven_day,
            seven_day_countdown,
            seven_day_clock,
            cache_age.as_deref(),
        );
    });
}

fn render_codex_account_stacked(
    ui: &mut egui::Ui,
    account: &Value,
    content_width: f32,
    last_used_profile: Option<&str>,
    active_profile: Option<&str>,
    profile_settings: &[(String, Option<String>, Option<String>)],
    pokemon: &mut PokemonCache,
    context: &egui::Context,
    poke_mode: bool,
) {
    let email = account.get("email").and_then(Value::as_str).unwrap_or("");
    let precisa_relogin = account
        .get("status")
        .and_then(Value::as_str)
        .is_some_and(|status| status != "ok");
    let last_used = last_used_profile
        .is_some_and(|profile| account.get("perfil").and_then(Value::as_str) == Some(profile));
    let ativa = active_profile
        .is_some_and(|profile| account.get("perfil").and_then(Value::as_str) == Some(profile));
    let pode_usar = !ativa && account.get("status").and_then(Value::as_str) == Some("ok");
    let info = pokemon.info("codex", email);
    let texture = poke_mode
        .then(|| pokemon.texture(context, "codex", email, false).cloned())
        .flatten();
    let species = poke_mode
        .then(|| info.as_ref().map(|info| info.name.clone()))
        .flatten();
    let primeira = quota_percent_at(account, 0);
    let segunda = quota_percent_at(account, 1);
    let primeira_countdown = quota_countdown_at(account, 0);
    let segunda_countdown = quota_countdown_at(account, 1);
    let primeira_clock = quota_clock_at(account, 0);
    let segunda_clock = quota_clock_at(account, 1);
    let primeira_age = quota_window_at(account, 0).map(window_label);
    let segunda_age = quota_window_at(account, 1).map(window_label);
    let plan = account
        .get("plano")
        .and_then(Value::as_str)
        .or_else(|| info.as_ref().and_then(|info| info.plan.as_deref()))
        .unwrap_or("ausente");
    let settings = account
        .get("perfil")
        .and_then(Value::as_str)
        .and_then(|profile| {
            profile_settings
                .iter()
                .find(|(known_profile, _, _)| known_profile == profile)
        });
    let model = settings.and_then(|(_, model, _)| model.as_deref());
    let effort = settings.and_then(|(_, _, effort)| effort.as_deref());
    let width = content_width;

    ui.horizontal(|ui| {
        if poke_mode {
            if let Some(texture) = texture.as_ref() {
                ui.add(
                    egui::Image::new((texture.id(), egui::vec2(40.0, 40.0)))
                        .fit_to_exact_size(egui::vec2(40.0, 40.0))
                        .texture_options(egui::TextureOptions::NEAREST),
                );
            } else {
                ui.add_space(ACCOUNT_SPRITE_SIZE);
            }
        }
        let marker = if ativa { "●" } else { " " };
        let marker_response = ui.add_sized(
            [ACCOUNT_CURSOR_WIDTH, 20.0],
            egui::Label::new(data_text(marker).color(hp_ok())),
        );
        if ativa {
            marker_response.on_hover_text("conta no comando");
        }
        let reserved = if poke_mode { ACCOUNT_SPRITE_SIZE } else { 0.0 }
            + ACCOUNT_CURSOR_WIDTH
            + ui.spacing().item_spacing.x * 2.0;
        ui.scope(|ui| {
            ui.set_max_width((width - reserved).max(0.0));
            ui.add(egui::Label::new(data_text(email)).wrap(true));
        });
    });

    ui.horizontal_wrapped(|ui| {
        if let Some(species) = species.as_deref() {
            ui.label(pixel_text(species, 10.0));
        }
        if last_used {
            ui.label(dim_text("· última usada"));
        }
        if precisa_relogin {
            ui.label(dim_text("relogar"));
        }
        if ativa {
            ui.label(dim_text("ativa"));
        }
        ui.label(data_text(plan));
        if let Some(profile) = account.get("perfil").and_then(Value::as_str) {
            if let Some(model) = model {
                let next = next_model(model);
                if ui
                    .add(egui::Button::new(data_text(model)))
                    .on_hover_cursor(egui::CursorIcon::PointingHand)
                    .on_hover_text(format!("clique para trocar o modelo · próximo: {next}"))
                    .clicked()
                {
                    let next = next.to_owned();
                    let next_effort = (effort == Some("ultra") && next != "gpt-5.6-sol")
                        .then(|| "high".to_owned());
                    write_config_scalars(
                        profile.to_owned(),
                        Some(next),
                        next_effort.or_else(|| effort.map(str::to_owned)),
                    );
                }
            } else {
                ui.label(data_text("—"));
            }
            if let (Some(effort), Some(model)) = (effort, model) {
                let next = next_effort(effort, model);
                if ui
                    .add(egui::Button::new(data_text(effort)))
                    .on_hover_cursor(egui::CursorIcon::PointingHand)
                    .on_hover_text(format!("clique para trocar o effort · próximo: {next}"))
                    .clicked()
                {
                    write_config_scalars(profile.to_owned(), None, Some(next.to_owned()));
                }
            } else {
                ui.label(data_text("—"));
            }
        } else {
            ui.label(data_text("—"));
            ui.label(data_text("—"));
        }
        if pode_usar {
            if let Some(perfil) = account.get("perfil").and_then(Value::as_str) {
                if ui
                    .add(egui::Button::new(pixel_text("usar", 9.0)))
                    .on_hover_cursor(egui::CursorIcon::PointingHand)
                    .on_hover_text("usar este perfil do Codex · vale só para novos terminais")
                    .clicked()
                {
                    let perfil = perfil.to_owned();
                    thread::spawn(move || {
                        let Some(home) = home_directory() else {
                            eprintln!("setx CODEX_HOME: diretorio home nao encontrado");
                            return;
                        };
                        let path = home.join(perfil);
                        if let Err(error) =
                            Command::new("setx").arg("CODEX_HOME").arg(&path).output()
                        {
                            eprintln!("setx CODEX_HOME {}: {error}", path.display());
                        }
                    });
                }
            }
        }
    });

    ui.horizontal(|ui| {
        ui.add_sized([42.0, 20.0], egui::Label::new(pixel_text("cota", 10.0)));
        usage_bar_aged(
            ui,
            primeira,
            primeira_countdown,
            primeira_clock,
            primeira_age.as_deref(),
        );
    });
    ui.horizontal(|ui| {
        ui.add_sized([42.0, 20.0], egui::Label::new(pixel_text("cota", 10.0)));
        usage_bar_aged(
            ui,
            segunda,
            segunda_countdown,
            segunda_clock,
            segunda_age.as_deref(),
        );
    });
}

fn render_codex_accounts(
    ui: &mut egui::Ui,
    accounts: &[Value],
    last_used_profile: Option<&str>,
    active_profile: Option<&str>,
    profile_settings: &[(String, Option<String>, Option<String>)],
    pokemon: &mut PokemonCache,
    context: &egui::Context,
    poke_mode: bool,
    content_width: f32,
    widths: Option<&[f32]>,
) {
    if accounts.is_empty() {
        dim_label(ui, content_width, "contas Codex ausentes");
        return;
    }

    if widths.is_none() {
        for (index, account) in accounts.iter().enumerate() {
            render_codex_account_stacked(
                ui,
                account,
                content_width,
                last_used_profile,
                active_profile,
                profile_settings,
                pokemon,
                context,
                poke_mode,
            );
            if index + 1 < accounts.len() {
                stacked_divider(ui);
            }
        }
        return;
    }
    let widths = widths.expect("larguras do grid do Codex");

    egui::Grid::new("codex_accounts")
        .num_columns(6)
        .min_col_width(0.0)
        .min_row_height(20.0)
        .spacing(egui::vec2(10.0, 8.0))
        .show(ui, |ui| {
            ui.add_sized(
                [widths[0], 20.0],
                egui::Label::new(pixel_text("conta", 10.0)),
            );
            ui.add_sized(
                [widths[1], 20.0],
                egui::Label::new(pixel_text("plano", 10.0)),
            );
            ui.add_sized(
                [widths[2], 20.0],
                egui::Label::new(pixel_text("modelo", 10.0)),
            );
            ui.add_sized(
                [widths[3], 20.0],
                egui::Label::new(pixel_text("effort", 10.0)),
            );
            ui.add_sized(
                [widths[4], 20.0],
                egui::Label::new(pixel_text("cota", 10.0)),
            );
            ui.add_sized(
                [widths[5], 20.0],
                egui::Label::new(pixel_text("cota", 10.0)),
            );
            ui.end_row();

            for account in accounts {
                let email = account.get("email").and_then(Value::as_str).unwrap_or("");
                let precisa_relogin = account
                    .get("status")
                    .and_then(Value::as_str)
                    .is_some_and(|status| status != "ok");
                let last_used = last_used_profile.is_some_and(|profile| {
                    account.get("perfil").and_then(Value::as_str) == Some(profile)
                });
                let ativa = active_profile.is_some_and(|profile| {
                    account.get("perfil").and_then(Value::as_str) == Some(profile)
                });
                let pode_usar = !ativa
                    && account.get("status").and_then(Value::as_str) == Some("ok");
                let info = pokemon.info("codex", email);
                let texture = poke_mode
                    .then(|| pokemon.texture(context, "codex", email, false).cloned())
                    .flatten();
                let species = poke_mode
                    .then(|| info.as_ref().map(|info| info.name.clone()))
                    .flatten();
                let primeira = quota_percent_at(account, 0);
                let segunda = quota_percent_at(account, 1);
                let primeira_countdown = quota_countdown_at(account, 0);
                let segunda_countdown = quota_countdown_at(account, 1);
                let primeira_clock = quota_clock_at(account, 0);
                let segunda_clock = quota_clock_at(account, 1);
                let primeira_age = quota_window_at(account, 0).map(window_label);
                let segunda_age = quota_window_at(account, 1).map(window_label);
                let plan = account
                    .get("plano")
                    .and_then(Value::as_str)
                    .or_else(|| info.as_ref().and_then(|info| info.plan.as_deref()))
                    .unwrap_or("ausente");
                let settings = account
                    .get("perfil")
                    .and_then(Value::as_str)
                    .and_then(|profile| {
                        profile_settings
                            .iter()
                            .find(|(known_profile, _, _)| known_profile == profile)
                    });
                let model = settings.and_then(|(_, model, _)| model.as_deref());
                let effort = settings.and_then(|(_, _, effort)| effort.as_deref());
                ui.allocate_ui_with_layout(
                    egui::vec2(widths[0], 40.0),
                    egui::Layout::left_to_right(egui::Align::Center),
                    |ui| {
                        if let Some(texture) = texture.as_ref() {
                            ui.add(
                                egui::Image::new((texture.id(), egui::vec2(40.0, 40.0)))
                                    .fit_to_exact_size(egui::vec2(40.0, 40.0))
                                    .texture_options(egui::TextureOptions::NEAREST),
                            );
                        } else {
                            ui.add_space(ACCOUNT_SPRITE_SIZE);
                        }
                        ui.label(egui::RichText::new(" ").font(egui::FontId::monospace(13.0)));
                        let info_width = (widths[0]
                            - ACCOUNT_SPRITE_SIZE
                            - ACCOUNT_CURSOR_WIDTH
                            - ui.spacing().item_spacing.x * 2.0)
                            .max(0.0);
                        ui.scope(|ui| {
                            ui.spacing_mut().item_spacing.y = 0.0;
                            ui.vertical(|ui| {
                                ui.add_sized(
                                    [info_width, 20.0],
                                    egui::Label::new(data_text(email)).truncate(true),
                                );
                                ui.horizontal(|ui| {
                                    let last_used_width = if last_used {
                                        text_width("· última usada") + ui.spacing().item_spacing.x
                                    } else {
                                        0.0
                                    };
                                    let relogin_width = if precisa_relogin {
                                        text_width("relogar") + ui.spacing().item_spacing.x
                                    } else {
                                        0.0
                                    };
                                    let ativa_width = if ativa {
                                        text_width("ativa") + ui.spacing().item_spacing.x
                                    } else {
                                        0.0
                                    };
                                    let usar_width = if pode_usar {
                                        text_width("usar") + ui.spacing().item_spacing.x
                                    } else {
                                        0.0
                                    };
                                    if let Some(species) = species.as_deref() {
                                        ui.add_sized(
                                            [(info_width
                                                - last_used_width
                                                - relogin_width
                                                - ativa_width
                                                - usar_width)
                                                .max(0.0), 20.0],
                                            egui::Label::new(pixel_text(species, 10.0))
                                                .truncate(true),
                                        );
                                    }
                                    if last_used {
                                        ui.add_sized(
                                            [text_width("· última usada"), 20.0],
                                            egui::Label::new(dim_text("· última usada"))
                                                .truncate(true),
                                            );
                                    }
                                    if precisa_relogin {
                                        ui.add_sized(
                                            [text_width("relogar"), 20.0],
                                            egui::Label::new(dim_text("relogar")).truncate(true),
                                        );
                                    }
                                    if ativa {
                                        ui.add_sized(
                                            [text_width("ativa"), 20.0],
                                            egui::Label::new(dim_text("ativa")).truncate(true),
                                        );
                                    }
                                    if pode_usar {
                                        if let Some(perfil) = account.get("perfil").and_then(Value::as_str) {
                                            if ui
                                                .add(egui::Button::new(pixel_text("usar", 9.0)))
                                                .on_hover_cursor(egui::CursorIcon::PointingHand)
                                                .on_hover_text("usar este perfil do Codex · vale só para novos terminais")
                                                .clicked()
                                            {
                                                let perfil = perfil.to_owned();
                                                thread::spawn(move || {
                                                    let Some(home) = home_directory() else {
                                                        eprintln!("setx CODEX_HOME: diretorio home nao encontrado");
                                                        return;
                                                    };
                                                    let path = home.join(perfil);
                                                    if let Err(error) = Command::new("setx")
                                                        .arg("CODEX_HOME")
                                                        .arg(&path)
                                                        .output()
                                                    {
                                                        eprintln!(
                                                            "setx CODEX_HOME {}: {error}",
                                                            path.display()
                                                        );
                                                    }
                                                });
                                            }
                                        }
                                    }
                                });
                            });
                        });
                    },
                );
                ui.add_sized(
                    [widths[1], 40.0],
                    egui::Label::new(data_text(plan)).truncate(true),
                );
                if let Some(profile) = account.get("perfil").and_then(Value::as_str) {
                    ui.allocate_ui_with_layout(
                        egui::vec2(widths[2], 24.0),
                        egui::Layout::left_to_right(egui::Align::Center),
                        |ui| {
                        if let Some(model) = model {
                            let next = next_model(model);
                            if ui
                                .add(egui::Button::new(data_text(model)))
                                .on_hover_cursor(egui::CursorIcon::PointingHand)
                                .on_hover_text(format!("clique para trocar o modelo · próximo: {next}"))
                                .clicked()
                            {
                                let next = next.to_owned();
                                let next_effort = (effort == Some("ultra") && next != "gpt-5.6-sol")
                                    .then(|| "high".to_owned());
                                write_config_scalars(
                                    profile.to_owned(),
                                    Some(next),
                                    next_effort.or_else(|| effort.map(str::to_owned)),
                                );
                            }
                        } else {
                            ui.label(data_text("—"));
                        }
                    });
                    ui.allocate_ui_with_layout(
                        egui::vec2(widths[3], 24.0),
                        egui::Layout::left_to_right(egui::Align::Center),
                        |ui| {
                        if let (Some(effort), Some(model)) = (effort, model) {
                            let next = next_effort(effort, model);
                            if ui
                                .add(egui::Button::new(data_text(effort)))
                                .on_hover_cursor(egui::CursorIcon::PointingHand)
                                .on_hover_text(format!("clique para trocar o effort · próximo: {next}"))
                                .clicked()
                            {
                                write_config_scalars(
                                    profile.to_owned(),
                                    None,
                                    Some(next.to_owned()),
                                );
                            }
                        } else {
                            ui.label(data_text("—"));
                        }
                    });
                } else {
                    ui.add_sized([widths[2], 24.0], egui::Label::new(data_text("—")));
                    ui.add_sized([widths[3], 24.0], egui::Label::new(data_text("—")));
                }
                ui.allocate_ui_with_layout(
                    egui::vec2(widths[4], 20.0),
                    egui::Layout::left_to_right(egui::Align::Center),
                    |ui| {
                        usage_bar_aged(
                            ui,
                            primeira,
                            primeira_countdown,
                            primeira_clock,
                            primeira_age.as_deref(),
                        )
                    },
                );
                ui.allocate_ui_with_layout(
                    egui::vec2(widths[5], 20.0),
                    egui::Layout::left_to_right(egui::Align::Center),
                    |ui| {
                        usage_bar_aged(
                            ui,
                            segunda,
                            segunda_countdown,
                            segunda_clock,
                            segunda_age.as_deref(),
                        )
                    },
                );
                ui.end_row();
            }
        });
}

fn show_codex(
    ui: &mut egui::Ui,
    content_width: f32,
    status: Option<&Result<Value, String>>,
    last_used_profile: Option<&str>,
    active_profile: Option<&str>,
    profile_settings: &[(String, Option<String>, Option<String>)],
    pokemon: &mut PokemonCache,
    context: &egui::Context,
    poke_mode: bool,
) {
    section_frame(ui, "CODEX", content_width, |ui, content_width| {
        let value = match status {
            None => {
                dim_label(ui, content_width, "carregando…");
                return;
            }
            Some(Err(error)) => {
                error_label(ui, content_width, error);
                return;
            }
            Some(Ok(value)) => value,
        };
        let Some(codex) = value.get("codex") else {
            error_label(ui, content_width, "status.py: JSON sem codex");
            return;
        };
        let Some(accounts) = codex.get("contas").and_then(Value::as_array) else {
            error_label(ui, content_width, "status.py: JSON sem codex.contas[]");
            return;
        };
        let cols = [
            (200.0, CODEX_ACCOUNT_WIDTH),
            (90.0, CODEX_PLAN_WIDTH),
            (110.0, CODEX_MODEL_WIDTH),
            (70.0, CODEX_EFFORT_WIDTH),
            (MIN_CLAUDE_QUOTA_WIDTH, CODEX_QUOTA_WIDTH),
            (MIN_CLAUDE_QUOTA_WIDTH, CODEX_QUOTA_WIDTH),
        ];
        let widths = column_widths(content_width, CODEX_GRID_GAPS, &cols);
        render_codex_accounts(
            ui,
            accounts,
            last_used_profile,
            active_profile,
            profile_settings,
            pokemon,
            context,
            poke_mode,
            content_width,
            widths.as_deref(),
        );
    });
}

fn render_opencodex_account_stacked(
    ui: &mut egui::Ui,
    account: &OpenCodexAccount,
    content_width: f32,
) {
    let width = content_width;
    ui.horizontal(|ui| {
        let marker = if account.active { "●" } else { " " };
        let marker_response = ui.add_sized(
            [ACCOUNT_CURSOR_WIDTH, 20.0],
            egui::Label::new(data_text(marker).color(hp_ok())),
        );
        if account.active {
            marker_response.on_hover_text("conta no comando");
        }
        ui.scope(|ui| {
            ui.set_max_width((width - ACCOUNT_CURSOR_WIDTH - ui.spacing().item_spacing.x).max(0.0));
            ui.add(egui::Label::new(data_text(&account.id)).wrap(true));
        });
    });
    ui.horizontal(|ui| {
        ui.add_sized([34.0, 20.0], egui::Label::new(pixel_text("5h", 10.0)));
        let short_countdown = account.short_reset_at.and_then(format_reset_countdown);
        let short_clock = account.short_reset_at.and_then(format_reset_clock);
        usage_bar_aged(
            ui,
            account.short_percent,
            short_countdown,
            short_clock,
            None,
        );
    });
    ui.horizontal(|ui| {
        ui.add_sized([34.0, 20.0], egui::Label::new(pixel_text("7d", 10.0)));
        let weekly_countdown = account.weekly_reset_at.and_then(format_reset_countdown);
        let weekly_clock = account.weekly_reset_at.and_then(format_reset_clock);
        usage_bar_aged(
            ui,
            account.weekly_percent,
            weekly_countdown,
            weekly_clock,
            None,
        );
    });
}

fn show_opencodex(ui: &mut egui::Ui, content_width: f32, snapshot: &OpenCodexSnapshot) {
    section_frame(ui, "OPENCODEX", content_width, |ui, content_width| {
        ui.horizontal_wrapped(|ui| {
            ui.label(pixel_text("provedor", 10.0));
            ui.label(data_text(
                snapshot.default_provider.as_deref().unwrap_or("—"),
            ));
            ui.add_space(12.0);
            ui.label(pixel_text("modo", 10.0));
            ui.label(data_text(snapshot.mode.as_deref().unwrap_or("—")));
            ui.add_space(12.0);
            ui.label(pixel_text("modelo padrão", 10.0));
            ui.label(data_text(
                snapshot
                    .default_model
                    .as_deref()
                    .unwrap_or("modelo não disponível"),
            ));
        });
        ui.add_space(10.0);
        if snapshot.accounts.is_empty() {
            dim_label(ui, content_width, "contas de cota ausentes");
            return;
        }

        let cols = [
            (240.0, OPENCODEX_ACCOUNT_WIDTH),
            (MIN_CLAUDE_QUOTA_WIDTH, OPENCODEX_QUOTA_WIDTH),
            (MIN_CLAUDE_QUOTA_WIDTH, OPENCODEX_QUOTA_WIDTH),
        ];
        match column_widths(content_width, OPENCODEX_GRID_GAPS, &cols) {
            Some(widths) => {
                egui::Grid::new("opencodex_accounts")
                    .num_columns(3)
                    .min_col_width(0.0)
                    .min_row_height(20.0)
                    .spacing(egui::vec2(10.0, 8.0))
                    .show(ui, |ui| {
                        ui.add_sized(
                            [widths[0], 20.0],
                            egui::Label::new(pixel_text("conta", 10.0)),
                        );
                        ui.add_sized([widths[1], 20.0], egui::Label::new(pixel_text("5h", 10.0)));
                        ui.add_sized([widths[2], 20.0], egui::Label::new(pixel_text("7d", 10.0)));
                        ui.end_row();

                        for account in &snapshot.accounts {
                            ui.allocate_ui_with_layout(
                                egui::vec2(widths[0], 40.0),
                                egui::Layout::left_to_right(egui::Align::Center),
                                |ui| {
                                    let marker = if account.active { "●" } else { " " };
                                    let marker_response = ui.add_sized(
                                        [ACCOUNT_CURSOR_WIDTH, 20.0],
                                        egui::Label::new(data_text(marker).color(hp_ok())),
                                    );
                                    if account.active {
                                        marker_response.on_hover_text("conta no comando");
                                    }
                                    let info_width = (widths[0]
                                        - ACCOUNT_CURSOR_WIDTH
                                        - ui.spacing().item_spacing.x)
                                        .max(0.0);
                                    ui.add_sized(
                                        [info_width, 20.0],
                                        egui::Label::new(data_text(&account.id)).truncate(true),
                                    );
                                },
                            );
                            let short_countdown =
                                account.short_reset_at.and_then(format_reset_countdown);
                            let short_clock = account.short_reset_at.and_then(format_reset_clock);
                            ui.allocate_ui_with_layout(
                                egui::vec2(widths[1], 20.0),
                                egui::Layout::left_to_right(egui::Align::Center),
                                |ui| {
                                    usage_bar_aged(
                                        ui,
                                        account.short_percent,
                                        short_countdown,
                                        short_clock,
                                        None,
                                    )
                                },
                            );
                            let weekly_countdown =
                                account.weekly_reset_at.and_then(format_reset_countdown);
                            let weekly_clock = account.weekly_reset_at.and_then(format_reset_clock);
                            ui.allocate_ui_with_layout(
                                egui::vec2(widths[2], 20.0),
                                egui::Layout::left_to_right(egui::Align::Center),
                                |ui| {
                                    usage_bar_aged(
                                        ui,
                                        account.weekly_percent,
                                        weekly_countdown,
                                        weekly_clock,
                                        None,
                                    )
                                },
                            );
                            ui.end_row();
                        }
                    });
            }
            None => {
                for (index, account) in snapshot.accounts.iter().enumerate() {
                    render_opencodex_account_stacked(ui, account, content_width);
                    if index + 1 < snapshot.accounts.len() {
                        stacked_divider(ui);
                    }
                }
            }
        }
    });
}

fn show_free_tier(ui: &mut egui::Ui, content_width: f32, status: Option<&Result<Value, String>>) {
    section_frame(ui, "FREE TIER", content_width, |ui, content_width| {
        match status {
            None => dim_label(ui, content_width, "carregando…"),
            Some(Err(error)) => error_label(ui, content_width, error),
            Some(Ok(value)) => {
                let free = value.get("free_tier");
                metric_row(
                    ui,
                    content_width,
                    "nvidia",
                    nested_display(free, &["nvidia", "usadas"]),
                    nested_display(free, &["nvidia", "limite"]),
                );
                metric_row(
                    ui,
                    content_width,
                    "gemini",
                    nested_display(free, &["gemini", "usadas"]),
                    nested_display(free, &["gemini", "limite"]),
                );
            }
        };
    });
}

fn show_context(ui: &mut egui::Ui, content_width: f32, status: Option<&Result<Value, String>>) {
    section_frame(ui, "CONTEXTO", content_width, |ui, content_width| {
        match status {
            None => dim_label(ui, content_width, "carregando…"),
            Some(Err(error)) => error_label(ui, content_width, error),
            Some(Ok(value)) => {
                ui.horizontal(|ui| {
                    ui.label(pixel_text("tokens", 10.0));
                    ui.add_space(8.0);
                    let tokens = format_number(
                        nested_number_opt(value.get("codex"), &["janela_tokens"]).unwrap_or(0.0),
                    );
                    let text = value
                        .get("codex")
                        .and_then(|codex| quota_window_at(codex, 0))
                        .map(|window| format!("{tokens} · {}", window_label(window)))
                        .unwrap_or(tokens);
                    let value_width = (content_width
                        - text_width("tokens")
                        - 8.0
                        - ui.spacing().item_spacing.x * 2.0)
                        .max(0.0);
                    data_label(ui, value_width, text);
                });
            }
        };
    });
}

fn section_frame(
    ui: &mut egui::Ui,
    title: &str,
    content_width: f32,
    add_contents: impl FnOnce(&mut egui::Ui, f32),
) {
    egui::Frame::none()
        .fill(panel())
        .stroke(egui::Stroke::new(2.0_f32, border()))
        .rounding(egui::Rounding::same(0.0))
        .inner_margin(egui::Margin::symmetric(14.0, 12.0))
        .show(ui, |ui| {
            ui.set_width(content_width);
            ui.add_sized(
                [content_width, 16.0],
                egui::Label::new(pixel_text(title, 13.0)),
            );
            ui.add_space(10.0);
            add_contents(ui, content_width);
        });
}

fn metric_row(ui: &mut egui::Ui, content_width: f32, label: &str, used: String, limit: String) {
    let value_width = (content_width - 72.0 - ui.spacing().item_spacing.x).max(0.0);
    ui.horizontal(|ui| {
        ui.add_sized([72.0, 20.0], egui::Label::new(pixel_text(label, 10.0)));
        data_label(ui, value_width, format!("{used} / {limit}"));
    });
}

fn pixel_font(size: f32) -> egui::FontId {
    egui::FontId::new(size, egui::FontFamily::Name("silkscreen".into()))
}

fn pixel_text(text: impl Into<String>, size: f32) -> egui::RichText {
    egui::RichText::new(text)
        .font(pixel_font(size))
        .color(text_color())
}

fn data_text(text: impl Into<String>) -> egui::RichText {
    egui::RichText::new(text)
        .font(egui::FontId::monospace(13.0))
        .color(text_color())
}

fn dim_text(text: impl Into<String>) -> egui::RichText {
    egui::RichText::new(text)
        .font(egui::FontId::monospace(13.0))
        .color(dim_color())
}

fn data_label(ui: &mut egui::Ui, width: f32, text: impl Into<String>) -> egui::Response {
    ui.add_sized(
        [width.max(0.0), 20.0],
        egui::Label::new(data_text(text)).truncate(true),
    )
}

fn dim_label(ui: &mut egui::Ui, width: f32, text: impl Into<String>) {
    ui.add_sized(
        [width.max(0.0), 20.0],
        egui::Label::new(dim_text(text)).truncate(true),
    );
}

fn error_label(ui: &mut egui::Ui, width: f32, error: &str) {
    ui.add_sized(
        [width.max(0.0), 20.0],
        egui::Label::new(egui::RichText::new(error).color(hp_low())).truncate(true),
    );
}

/// Igual a usage_bar, mas anexa a idade quando o valor veio de lastGoodUsage.
///
/// Dado velho nao pode parecer fresco: uma conta em relogin_required tem cache de dias, e
/// mostrar "26%" limpo levaria a decisao errada. Com marca fica "26% 3d".
fn usage_bar_aged(
    ui: &mut egui::Ui,
    value: Option<f64>,
    countdown: Option<String>,
    clock: Option<String>,
    age: Option<&str>,
) {
    usage_bar_inner(ui, value, countdown, clock, age);
}

fn usage_bar_inner(
    ui: &mut egui::Ui,
    value: Option<f64>,
    countdown: Option<String>,
    clock: Option<String>,
    age: Option<&str>,
) {
    let gap = ui.spacing().item_spacing.x;
    ui.horizontal(|ui| {
        ui.spacing_mut().item_spacing.x = 0.0;
        let label = match (value, age) {
            (Some(value), Some(age)) => format!("{value:.0}% {age}"),
            (Some(value), None) => format!("{value:.0}%"),
            (None, _) => "—".to_owned(),
        };
        let label_width = PERCENT_WIDTH.max(text_width(&label));
        // A largura do rotulo entra no orcamento porque cresce quando ha age.
        let fixed_width = gap + label_width;
        let reset_budget = (ui.available_width() - fixed_width - MIN_BAR_WIDTH).max(0.0);
        let reset_width =
            reset_details_width_for(countdown.as_deref(), clock.as_deref(), reset_budget);
        let bar_width = (ui.available_width() - fixed_width - reset_width).max(MIN_BAR_WIDTH);
        discrete_blocks(
            ui,
            bar_width,
            value.map(normalized_percent),
            value.map(hp_color),
        );
        ui.add_space(gap);
        ui.add_sized([label_width, 20.0], egui::Label::new(data_text(label)));
        if reset_width > 0.0 {
            add_reset_details(ui, countdown.as_deref(), clock.as_deref(), reset_width);
        }
    });
}

fn reset_details_width_for(countdown: Option<&str>, clock: Option<&str>, budget: f32) -> f32 {
    if countdown.is_none() && clock.is_none() {
        return 0.0;
    }
    let prefix = 4.0;
    if budget <= prefix {
        return 0.0;
    }
    let countdown_width = countdown
        .map(|value| text_width(&format!("· {value}")))
        .unwrap_or(0.0);
    let clock_width = clock.map(text_width).unwrap_or(0.0);
    let available = budget - prefix;

    // The exact clock is the first thing to drop.  Keep the countdown and
    // let its label truncate when that is all the remaining width allows.
    if countdown.is_some() {
        if clock.is_some() && available >= countdown_width + 4.0 + clock_width {
            prefix + countdown_width + 4.0 + clock_width
        } else {
            prefix + available.min(countdown_width)
        }
    } else {
        prefix + available.min(clock_width)
    }
}

fn add_reset_details(
    ui: &mut egui::Ui,
    countdown: Option<&str>,
    clock: Option<&str>,
    reset_width: f32,
) {
    let countdown = countdown.map(|value| format!("· {value}"));
    let clock = clock.map(str::to_owned);
    if countdown.is_none() && clock.is_none() {
        return;
    }

    ui.add_space(4.0);
    let available = (reset_width - 4.0).max(0.0);
    let countdown_width = countdown.as_deref().map(text_width).unwrap_or(0.0);
    let clock_width = clock.as_deref().map(text_width).unwrap_or(0.0);
    if let Some(countdown) = countdown.as_deref() {
        let width = available.min(countdown_width);
        if width > 0.0 {
            ui.add_sized(
                [width, 20.0],
                egui::Label::new(dim_text(countdown)).truncate(true),
            );
        }
    }
    if let Some(clock) = clock.as_deref() {
        let used = if countdown.is_some() {
            // O +4 tem que casar com o add_space abaixo, senao o relogio cola no countdown
            // ("5d 22h06/09 07:59" era o sintoma).
            ui.add_space(4.0);
            countdown_width + 8.0
        } else {
            0.0
        };
        let width = available - used;
        if width >= clock_width {
            ui.add_sized(
                [clock_width, 20.0],
                egui::Label::new(dim_text(clock)).truncate(true),
            );
        } else if countdown.is_none() && width > 0.0 {
            ui.add_sized(
                [width, 20.0],
                egui::Label::new(dim_text(clock)).truncate(true),
            );
        }
    }
}

fn text_width(text: &str) -> f32 {
    text.chars().count() as f32 * 8.0 + 2.0
}

fn loading_blocks(ui: &mut egui::Ui) {
    let (rect, response) = allocate_blocks(ui, 84.0);
    let time = ui.input(|input| input.time) as f32;
    let head = (time * 2.4) % 10.0;
    let empty = egui::Color32::from_rgba_unmultiplied(0x4A, 0x5D, 0x3F, 80);
    paint_blocks(
        ui,
        rect,
        |index| {
            let distance = (head - index as f32 + 10.0) % 10.0;
            distance < 3.8
        },
        |index| {
            let distance = (head - index as f32 + 10.0) % 10.0;
            let intensity = (1.0 - distance / 3.8).clamp(0.0, 1.0);
            egui::Color32::from_rgba_unmultiplied(0xC8, 0xD6, 0xBC, (intensity * 255.0) as u8)
        },
        |_| empty,
    );
    response.on_hover_text("em andamento");
}

fn discrete_blocks(
    ui: &mut egui::Ui,
    width: f32,
    value: Option<f64>,
    color: Option<egui::Color32>,
) {
    let (rect, response) = allocate_blocks(ui, width);
    let empty = egui::Color32::from_rgba_unmultiplied(0x4A, 0x5D, 0x3F, 80);
    let filled = value
        .map(|value| ((value / 10.0).round() as usize).min(10))
        .unwrap_or(0);
    paint_blocks(
        ui,
        rect,
        |index| index < filled,
        |_| color.unwrap_or(empty),
        |_| empty,
    );
    if let Some(value) = value {
        response.on_hover_text(format!("{value:.0}%"));
    }
}

fn allocate_blocks(ui: &mut egui::Ui, width: f32) -> (egui::Rect, egui::Response) {
    ui.allocate_exact_size(egui::vec2(width, 12.0), egui::Sense::hover())
}

fn paint_blocks(
    ui: &mut egui::Ui,
    rect: egui::Rect,
    filled: impl Fn(usize) -> bool,
    filled_color: impl Fn(usize) -> egui::Color32,
    empty_color: impl Fn(usize) -> egui::Color32,
) {
    let gap = 3.0;
    let block_width = (rect.width() - gap * 9.0) / 10.0;
    for index in 0..10 {
        let left = rect.left() + index as f32 * (block_width + gap);
        let block = egui::Rect::from_min_size(
            egui::pos2(left, rect.top()),
            egui::vec2(block_width, rect.height()),
        );
        let color = if filled(index) {
            filled_color(index)
        } else {
            empty_color(index)
        };
        ui.painter().rect_filled(block, 0.0, color);
    }
}

fn normalized_percent(value: f64) -> f64 {
    if value.is_finite() {
        value.clamp(0.0, 100.0)
    } else {
        0.0
    }
}

fn hp_color(value: f64) -> egui::Color32 {
    if value > 85.0 {
        hp_low()
    } else if value >= 60.0 {
        hp_mid()
    } else {
        hp_ok()
    }
}

fn string_field(value: &Value, field: &str) -> String {
    value
        .get(field)
        .and_then(Value::as_str)
        .map(str::to_owned)
        .unwrap_or_else(|| "—".to_owned())
}

fn number_field(value: &Value, field: &str) -> f64 {
    value.get(field).and_then(as_number).unwrap_or(0.0)
}

fn nested_number(value: &Value, path: &[&str]) -> Option<f64> {
    let mut current = value;
    for key in path {
        current = current.get(*key)?;
    }
    as_number(current)
}

fn nested_number_opt(value: Option<&Value>, path: &[&str]) -> Option<f64> {
    value.and_then(|value| nested_number(value, path))
}

fn nested_string<'a>(value: &'a Value, path: &[&str]) -> Option<&'a str> {
    let mut current = value;
    for key in path {
        current = current.get(*key)?;
    }
    current.as_str()
}

/// Qual bloco de consumo usar, e se ele veio do cache.
///
/// O cswap devolve `usage: null` quando a conta esta em relogin_required, mas guarda o ultimo
/// valor bom em `lastGoodUsage` (mesma estrutura interna: fiveHour/sevenDay com pct, countdown,
/// clock). Antes o painel lia so `usage` e mostrava "—" nessas contas.
fn usage_source(account: &Value) -> (Option<&Value>, bool) {
    match account.get("usage") {
        Some(usage) if !usage.is_null() => (Some(usage), false),
        _ => match account.get("lastGoodUsage") {
            Some(cache) if !cache.is_null() => (Some(cache), true),
            _ => (None, false),
        },
    }
}

/// Idade do dado de cache, compacta. Vazio quando o valor e fresco.
fn usage_age(account: &Value) -> Option<String> {
    let (_, from_cache) = usage_source(account);
    if !from_cache {
        return None;
    }
    let seconds = account.get("lastGoodAgeSeconds").and_then(Value::as_f64)?;
    let minutes = seconds / 60.0;
    Some(if minutes < 60.0 {
        format!("{}m", minutes.max(1.0) as i64)
    } else if minutes < 1440.0 {
        format!("{}h", (minutes / 60.0) as i64)
    } else {
        format!("{}d", (minutes / 1440.0) as i64)
    })
}

fn usage_percent(account: &Value, window: &str) -> Option<f64> {
    let (source, _) = usage_source(account);
    nested_number_opt(source, &[window, "pct"])
}

fn usage_text(account: &Value, window: &str, field: &str) -> Option<String> {
    let (source, _) = usage_source(account);
    nested_string(source?, &[window, field])
        .filter(|text| !text.is_empty())
        .map(str::to_owned)
}

fn usage_clock(account: &Value, window: &str) -> Option<String> {
    usage_text(account, window, "clock").map(|clock| format_claude_clock(&clock))
}

fn format_claude_clock(clock: &str) -> String {
    let parts: Vec<_> = clock.split_whitespace().collect();
    if parts.len() != 3 {
        return clock.to_owned();
    }
    let month = match parts[0].to_ascii_lowercase().as_str() {
        "jan" => 1,
        "feb" => 2,
        "mar" => 3,
        "apr" => 4,
        "may" => 5,
        "jun" => 6,
        "jul" => 7,
        "aug" => 8,
        "sep" => 9,
        "oct" => 10,
        "nov" => 11,
        "dec" => 12,
        _ => return clock.to_owned(),
    };
    let Ok(day) = parts[1].parse::<u32>() else {
        return clock.to_owned();
    };
    let valid_time = parts[2].split_once(':').is_some_and(|(hour, minute)| {
        hour.parse::<u32>().is_ok_and(|hour| hour < 24)
            && minute.parse::<u32>().is_ok_and(|minute| minute < 60)
            && minute.len() == 2
            && hour.len() == 2
    });
    if !(1..=31).contains(&day) || !valid_time {
        clock.to_owned()
    } else {
        format!("{day:02}/{month:02} {}", parts[2])
    }
}

/// A conta reporta as janelas em ordem crescente (primary, secondary).
/// Indexar por posicao, e nao por duracao, sobrevive a troca de plano:
/// o free reporta uma unica janela de 43200 min, o team reporta 300 + 10080.
fn quota_at(account: &Value, index: usize) -> Option<&Value> {
    account.get("quotas")?.as_array()?.get(index)
}

// Leio o registro, nao env::var, pois setx nao atualiza este processo; expando o caminho para nao literalizar %USERPROFILE%.
fn read_codex_active_profile() -> Option<String> {
    let output = Command::new("reg")
        .args(["query", "HKCU\\Environment", "/v", "CODEX_HOME"])
        .output()
        .ok()?;
    if !output.status.success() {
        return Some(".codex".to_owned());
    }
    let stdout = String::from_utf8(output.stdout).ok()?;
    let value = stdout.lines().find_map(|line| {
        let rest = line.trim().strip_prefix("CODEX_HOME")?.trim_start();
        let rest = rest
            .strip_prefix("REG_SZ")
            .or_else(|| rest.strip_prefix("REG_EXPAND_SZ"))?
            .trim();
        (!rest.is_empty()).then_some(rest)
    })?;
    Path::new(value)
        .file_name()
        .and_then(|name| name.to_str())
        .map(str::to_owned)
}

fn quota_percent_at(account: &Value, index: usize) -> Option<f64> {
    quota_at(account, index)
        .and_then(|quota| quota.get("pct"))
        .and_then(as_number)
}

fn quota_countdown_at(account: &Value, index: usize) -> Option<String> {
    quota_at(account, index)
        .and_then(|quota| quota.get("reseta_em"))
        .and_then(as_number)
        .and_then(format_reset_countdown)
}

fn quota_clock_at(account: &Value, index: usize) -> Option<String> {
    quota_at(account, index)
        .and_then(|quota| quota.get("reseta_em"))
        .and_then(as_number)
        .and_then(format_reset_clock)
}

fn quota_window_at(account: &Value, index: usize) -> Option<f64> {
    quota_at(account, index)
        .and_then(|quota| quota.get("janela"))
        .and_then(as_number)
}

/// 300 -> "5h", 10080 -> "7d", 43200 -> "30d". Espelha status.py:459.
fn window_label(minutes: f64) -> String {
    let minutes = minutes as u64;
    if minutes < 60 {
        format!("{minutes}min")
    } else if minutes < 1440 {
        format!("{}h", minutes / 60)
    } else {
        format!("{}d", minutes / 1440)
    }
}

fn format_reset_countdown(resets_at: f64) -> Option<String> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()?
        .as_secs_f64();
    let mut seconds = (resets_at - now).max(0.0) as u64;
    let days = seconds / 86_400;
    seconds %= 86_400;
    let hours = seconds / 3_600;
    seconds %= 3_600;
    let minutes = seconds / 60;

    if days > 0 {
        Some(if hours > 0 {
            format!("{days}d {hours}h")
        } else {
            format!("{days}d")
        })
    } else if hours > 0 {
        Some(if minutes > 0 {
            format!("{hours}h {minutes}m")
        } else {
            format!("{hours}h")
        })
    } else {
        Some(format!("{minutes}m"))
    }
}

#[derive(Clone, Copy)]
struct LocalDateTime {
    year: u16,
    month: u16,
    day: u16,
    hour: u16,
    minute: u16,
}

#[cfg(windows)]
fn local_date_time(epoch: f64) -> Option<LocalDateTime> {
    const WINDOWS_EPOCH_OFFSET: f64 = 11_644_473_600.0;
    if !epoch.is_finite() || epoch < -WINDOWS_EPOCH_OFFSET {
        return None;
    }
    let ticks = ((epoch + WINDOWS_EPOCH_OFFSET) * 10_000_000.0) as u64;
    let utc = FILETIME {
        dwLowDateTime: ticks as u32,
        dwHighDateTime: (ticks >> 32) as u32,
    };
    let mut utc_system_time: SYSTEMTIME = unsafe { std::mem::zeroed() };
    let mut local_system_time: SYSTEMTIME = unsafe { std::mem::zeroed() };
    let converted = unsafe {
        FileTimeToSystemTime(&utc, &mut utc_system_time) != 0
            && SystemTimeToTzSpecificLocalTime(
                std::ptr::null(),
                &utc_system_time,
                &mut local_system_time,
            ) != 0
    };
    converted.then_some(LocalDateTime {
        year: local_system_time.wYear,
        month: local_system_time.wMonth,
        day: local_system_time.wDay,
        hour: local_system_time.wHour,
        minute: local_system_time.wMinute,
    })
}

#[cfg(not(windows))]
fn local_date_time(_epoch: f64) -> Option<LocalDateTime> {
    None
}

fn format_reset_clock(resets_at: f64) -> Option<String> {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()?
        .as_secs_f64();
    let reset = local_date_time(resets_at)?;
    let today = local_date_time(now)?;
    Some(
        if reset.year == today.year && reset.month == today.month && reset.day == today.day {
            format!("{:02}:{:02}", reset.hour, reset.minute)
        } else {
            format!(
                "{:02}/{:02} {:02}:{:02}",
                reset.day, reset.month, reset.hour, reset.minute
            )
        },
    )
}

fn nested_display(value: Option<&Value>, path: &[&str]) -> String {
    nested_number_opt(value, path)
        .map(format_number)
        .unwrap_or_else(|| "—".to_owned())
}

fn as_number(value: &Value) -> Option<f64> {
    value
        .as_f64()
        .or_else(|| value.as_i64().map(|number| number as f64))
}

fn format_duration(seconds: f64) -> String {
    let total = seconds.max(0.0) as u64;
    format!("{:02}:{:02}", total / 60, total % 60)
}

fn format_number(number: f64) -> String {
    let number = number.max(0.0);
    if number >= 1_000_000.0 {
        format!("{:.1}M", number / 1_000_000.0)
    } else if number >= 1_000.0 {
        format!("{}k", (number / 1_000.0) as u64)
    } else {
        format!("{number:.0}")
    }
}

fn main() -> eframe::Result<()> {
    let options = eframe::NativeOptions {
        viewport: egui::ViewportBuilder::default()
            .with_inner_size([INITIAL_INNER_WIDTH, INITIAL_INNER_HEIGHT])
            .with_min_inner_size([MIN_INNER_WIDTH, MIN_INNER_HEIGHT])
            .with_resizable(true)
            .with_decorations(false)
            .with_icon(icone()),
        ..Default::default()
    };
    eframe::run_native(
        PANEL_TITLE,
        options,
        Box::new(|creation_context| Box::new(PanelApp::new(creation_context))),
    )
}

#[cfg(test)]
mod tests {
    use super::{column_widths, read_config_scalar, set_config_scalar};

    const SAMPLE: &str = "model = \"gpt-5.6-sol\"\r\nmodel_reasoning_effort = \"ultra\"\r\n\r\n[projects.'x']\r\ntrust_level = \"trusted\"\r\nmodel = \"gpt-5.6-luna\"\r\n";

    #[test]
    fn config_scalars_only_use_top_level_and_preserve_sections() {
        assert_eq!(
            read_config_scalar(SAMPLE, "model").as_deref(),
            Some("gpt-5.6-sol")
        );
        assert_eq!(
            read_config_scalar(SAMPLE, "model_reasoning_effort").as_deref(),
            Some("ultra")
        );

        let changed = set_config_scalar(SAMPLE, "model", "gpt-5.6-terra");
        assert!(changed.starts_with("model = \"gpt-5.6-terra\"\r\n"));
        assert!(changed.contains(
            "[projects.'x']\r\ntrust_level = \"trusted\"\r\nmodel = \"gpt-5.6-luna\"\r\n"
        ));

        let inserted = set_config_scalar(SAMPLE, "model_provider", "openai");
        assert!(inserted.starts_with("model_provider = \"openai\"\r\nmodel ="));
        assert!(inserted.ends_with("trust_level = \"trusted\"\r\nmodel = \"gpt-5.6-luna\"\r\n"));
    }

    #[test]
    fn column_widths_cabem() {
        let cols = [(2.0_f32, 4.0_f32), (3.0_f32, 6.0_f32)];
        let gaps = 5.0_f32;
        let min = 5.0_f32;
        let ideal = 10.0_f32;
        let at_min = column_widths(min + gaps, gaps, &cols).expect("cabem no minimo");
        let at_ideal = column_widths(ideal + gaps, gaps, &cols).expect("cabem no ideal");
        let intermediate = column_widths(7.5 + gaps, gaps, &cols).expect("cabem no intermediario");

        assert!((at_min.iter().sum::<f32>() - min).abs() < f32::EPSILON);
        assert!((at_ideal.iter().sum::<f32>() - ideal).abs() < f32::EPSILON);
        assert!(column_widths(min + gaps - 1.0, gaps, &cols).is_none());
        assert!(intermediate.iter().sum::<f32>() <= 7.5 + f32::EPSILON);
    }
}
