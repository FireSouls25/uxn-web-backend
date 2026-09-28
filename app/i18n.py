"""English + Spanish message catalog. New user-facing strings go here,
never inline in endpoints, so both languages stay in sync."""
from __future__ import annotations

SUPPORTED_LANGS = ("en", "es")
DEFAULT_LANG = "en"

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "error.unknown_target": "Unknown target '{target}'. Supported: {supported}.",
        "error.coming_soon": "Target '{target}' is coming soon and not served yet.",
        "error.version_mismatch": "Compiler mismatch: client expects etal {want}, server runs {have}.",
        "error.empty_files": "No files provided.",
        "error.too_many_files": "Too many files ({n}, max {max}).",
        "error.bad_entry": "Entry '{entry}' is not one of the provided files.",
        "error.bad_path": "Invalid path '{path}': must be relative, without '..', max {max} chars.",
        "error.bad_extension": "Invalid extension for '{path}': allowed: {allowed}.",
        "error.file_too_large": "File '{path}' exceeds {max} bytes.",
        "error.total_too_large": "Total upload exceeds {max} bytes.",
        "error.compile_failed": "Compilation failed.",
        "error.timeout": "Compilation timed out after {seconds}s.",
        "error.compiler_missing": "Compiler unavailable: {detail}.",
        "error.job_not_found": "Job '{job_id}' not found.",
        "error.unauthorized": "Invalid or missing API key.",
        "error.not_authenticated": "Login required.",
        "error.invalid_credentials": "Wrong email or password.",
        "error.email_taken": "That email is already registered.",
        "error.invalid_email": "That email address looks off.",
        "error.weak_password": "Password needs 8–72 characters.",
        "error.bad_name": "Pick a display name of 2–64 characters.",
        "error.invalid_token": "Invalid or expired token.",
        "error.rate_limited": "Rate limit exceeded. Retry in {seconds}s.",
        "error.server_busy": "Server busy compiling. Try again shortly.",
        "ok.compiled": "Compiled successfully.",
    },
    "es": {
        "error.unknown_target": "Destino desconocido '{target}'. Admitidos: {supported}.",
        "error.coming_soon": "El destino '{target}' llegará pronto y aún no está disponible.",
        "error.version_mismatch": "Versión incompatible: el cliente espera etal {want}, el servidor usa {have}.",
        "error.empty_files": "No se proporcionaron archivos.",
        "error.too_many_files": "Demasiados archivos ({n}, máximo {max}).",
        "error.bad_entry": "La entrada '{entry}' no está entre los archivos proporcionados.",
        "error.bad_path": "Ruta inválida '{path}': debe ser relativa, sin '..', máximo {max} caracteres.",
        "error.bad_extension": "Extensión inválida para '{path}': permitidas: {allowed}.",
        "error.file_too_large": "El archivo '{path}' supera los {max} bytes.",
        "error.total_too_large": "La subida total supera los {max} bytes.",
        "error.compile_failed": "La compilación falló.",
        "error.timeout": "La compilación excedió el límite de {seconds}s.",
        "error.compiler_missing": "Compilador no disponible: {detail}.",
        "error.job_not_found": "Trabajo '{job_id}' no encontrado.",
        "error.unauthorized": "Clave API inválida o ausente.",
        "error.not_authenticated": "Debes entrar primero.",
        "error.invalid_credentials": "Correo o contraseña incorrectos.",
        "error.email_taken": "Ese correo ya está registrado.",
        "error.invalid_email": "Ese correo parece incorrecto.",
        "error.weak_password": "La contraseña necesita 8–72 caracteres.",
        "error.bad_name": "Elige un nombre de 2–64 caracteres.",
        "error.invalid_token": "Token inválido o expirado.",
        "error.rate_limited": "Límite excedido. Reintente en {seconds}s.",
        "error.server_busy": "Servidor ocupado compilando. Intente de nuevo.",
        "ok.compiled": "Compilado correctamente.",
    },
}


def resolve_lang(request_lang: str | None, accept_language: str | None) -> str:
    """Explicit `lang` field wins, then the Accept-Language header."""
    if request_lang in SUPPORTED_LANGS:
        return request_lang
    if accept_language:
        first = accept_language.split(",")[0].strip().lower()
        short = first.split("-")[0]
        if short in SUPPORTED_LANGS:
            return short
    return DEFAULT_LANG


def t(key: str, lang: str, **kwargs: object) -> str:
    catalog = MESSAGES.get(lang, MESSAGES[DEFAULT_LANG])
    template = catalog.get(key, MESSAGES[DEFAULT_LANG].get(key, key))
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError):
        return template
