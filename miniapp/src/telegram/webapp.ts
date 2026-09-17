import { useEffect } from "react";
import type { TelegramWebApp } from "./telegram-web-app";

/** Доступ к window.Telegram.WebApp (null вне Telegram). */
export function getWebApp(): TelegramWebApp | null {
  if (typeof window === "undefined") return null;
  return window.Telegram?.WebApp ?? null;
}

/** Сырая initData-строка — единственный credential для backend. */
export function getInitData(): string {
  return getWebApp()?.initData ?? "";
}

function applyColorScheme(scheme: "light" | "dark"): void {
  const root = document.documentElement;
  root.classList.toggle("tg-dark", scheme === "dark");
  root.classList.toggle("tg-light", scheme === "light");
  root.style.colorScheme = scheme;
}

/**
 * Ранняя инициализация: ready() + expand() + тема.
 * Вызывать до рендера приложения (main.tsx).
 */
export function initTelegramWebApp(): void {
  const wa = getWebApp();
  if (!wa) return;
  try {
    wa.ready();
    wa.expand();
    applyColorScheme(wa.colorScheme);
    try {
      wa.setHeaderColor("bg_color");
    } catch {
      // старые клиенты могут не поддерживать строковые ключи цветов
    }
    wa.onEvent("themeChanged", () => applyColorScheme(wa.colorScheme));
  } catch {
    // не мешаем рендеру, если клиент ведёт себя нестандартно
  }
}

/**
 * Синхронизация нативной BackButton с навигацией.
 * visible=true → показать кнопку и повесить onBack; false → скрыть.
 */
export function useTelegramBackButton(visible: boolean, onBack: () => void): void {
  useEffect(() => {
    const wa = getWebApp();
    if (!wa) return;
    const bb = wa.BackButton;
    if (!visible) {
      bb.hide();
      return;
    }
    bb.onClick(onBack);
    bb.show();
    return () => {
      bb.offClick(onBack);
      bb.hide();
    };
  }, [visible, onBack]);
}

/** Лёгкий тактильный отклик (best-effort, без исключений). */
export function hapticNotification(type: "error" | "success" | "warning"): void {
  try {
    getWebApp()?.HapticFeedback?.notificationOccurred(type);
  } catch {
    // опциональная возможность
  }
}

/** Нативный confirm Telegram с fallback на window.confirm. */
export function confirmNative(message: string): Promise<boolean> {
  const wa = getWebApp();
  if (wa) {
    return new Promise((resolve) => {
      try {
        wa.showConfirm(message, (ok) => resolve(ok));
      } catch {
        resolve(window.confirm(message));
      }
    });
  }
  return Promise.resolve(window.confirm(message));
}
