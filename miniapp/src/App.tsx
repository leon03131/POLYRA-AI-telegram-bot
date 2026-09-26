import { useCallback } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HashRouter, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./api/auth";
import { useTelegramBackButton } from "./telegram/webapp";
import { Button, Spinner } from "./components";
import { HomePage } from "./pages/HomePage";
import { ChatsPage } from "./pages/ChatsPage";
import { ChatPage } from "./pages/ChatPage";
import { ChatSettingsPage } from "./pages/ChatSettingsPage";
import { MemoryPage } from "./pages/MemoryPage";
import { SettingsPage } from "./pages/SettingsPage";
import { AdminLayout } from "./admin/AdminLayout";
import { AdminDashboardPage } from "./admin/AdminDashboardPage";
import { AdminUsersPage } from "./admin/AdminUsersPage";
import { AdminModelsPage } from "./admin/AdminModelsPage";
import { AdminGeminiPage } from "./admin/AdminGeminiPage";
import { AdminProvidersPage } from "./admin/AdminProvidersPage";
import { AdminMemoryPage } from "./admin/AdminMemoryPage";
import { AdminSearchPage } from "./admin/AdminSearchPage";
import { AdminSystemPage } from "./admin/AdminSystemPage";
import { AdminAuditPage } from "./admin/AdminAuditPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 10_000,
    },
  },
});

/** Нативная BackButton Telegram: видна только на детальных страницах (/chats/:id). */
function BackButtonManager() {
  const location = useLocation();
  const navigate = useNavigate();
  const isDetail = /^\/chats\/[^/]+/.test(location.pathname);
  const canGoBack = location.key !== "default";

  const goBack = useCallback(() => {
    if (canGoBack) navigate(-1);
    else navigate("/chats");
  }, [canGoBack, navigate]);

  useTelegramBackButton(isDetail, goBack);
  return null;
}

interface TabDef {
  to: string;
  label: string;
  icon: string;
}

function TabBar({ isOwner }: { isOwner: boolean }) {
  const tabs: TabDef[] = [
    { to: "/", label: "Главная", icon: "🏠" },
    { to: "/chats", label: "Чаты", icon: "💬" },
    { to: "/memory", label: "Память", icon: "🧠" },
    { to: "/settings", label: "Настройки", icon: "⚙️" },
  ];
  if (isOwner) tabs.push({ to: "/admin", label: "Админ", icon: "🛠" });

  return (
    <nav className="tabbar">
      {tabs.map((t) => (
        <NavLink
          key={t.to}
          to={t.to}
          end={t.to === "/"}
          className={({ isActive }) => "tab" + (isActive ? " active" : "")}
        >
          <span className="tab-ico">{t.icon}</span>
          {t.label}
        </NavLink>
      ))}
    </nav>
  );
}

function NoTelegramScreen() {
  return (
    <div className="center-screen">
      <div style={{ fontSize: 48 }}>🔒</div>
      <h2 style={{ margin: 0 }}>Откройте через Telegram</h2>
      <p className="hint-text" style={{ maxWidth: 320 }}>
        Этот Mini App работает только внутри Telegram. Откройте бота и нажмите кнопку меню
        «Настройки».
      </p>
    </div>
  );
}

function ErrorScreen({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="center-screen">
      <div style={{ fontSize: 48 }}>⚠️</div>
      <h2 style={{ margin: 0 }}>Не удалось авторизоваться</h2>
      <p className="hint-text" style={{ maxWidth: 320 }}>
        {message}
      </p>
      <Button onClick={onRetry}>Повторить</Button>
    </div>
  );
}

function AppShell() {
  const auth = useAuth();
  const location = useLocation();
  // web5: экран чата (/chats/:id) — иммерсивный режим без таббара
  // (настройки чата живут на /chats/:id/settings, таббар там остаётся).
  const isChatScreen = /^\/chats\/[^/]+$/.test(location.pathname);

  if (auth.status === "loading") {
    return (
      <div className="center-screen">
        <Spinner />
      </div>
    );
  }
  if (auth.status === "no-telegram") return <NoTelegramScreen />;
  if (auth.status === "error") return <ErrorScreen message={auth.error ?? "Ошибка"} onRetry={auth.retry} />;

  return (
    <div className="app-shell">
      <BackButtonManager />
      <div className="page-container">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/chats" element={<ChatsPage />} />
          <Route path="/chats/:id" element={<ChatPage />} />
          <Route path="/chats/:id/settings" element={<ChatSettingsPage />} />
          <Route path="/memory" element={<MemoryPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="/admin" element={<AdminLayout />}>
            <Route index element={<AdminDashboardPage />} />
            <Route path="users" element={<AdminUsersPage />} />
            <Route path="models" element={<AdminModelsPage />} />
            <Route path="gemini" element={<AdminGeminiPage />} />
            <Route path="providers" element={<AdminProvidersPage />} />
            <Route path="memory" element={<AdminMemoryPage />} />
            <Route path="search" element={<AdminSearchPage />} />
            <Route path="system" element={<AdminSystemPage />} />
            <Route path="audit" element={<AdminAuditPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
      {!isChatScreen && <TabBar isOwner={auth.isOwner} />}
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <HashRouter>
          <AppShell />
        </HashRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}
