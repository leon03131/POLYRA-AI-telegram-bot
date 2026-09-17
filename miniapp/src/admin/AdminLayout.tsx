import { Navigate, NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../api/auth";

const SUBTABS = [
  { to: "/admin", label: "Дашборд", end: true },
  { to: "/admin/users", label: "Пользователи", end: false },
  { to: "/admin/gemini", label: "Gemini", end: false },
  { to: "/admin/providers", label: "Провайдеры", end: false },
  { to: "/admin/search", label: "Поиск", end: false },
  { to: "/admin/system", label: "Система", end: false },
  { to: "/admin/audit", label: "Аудит", end: false },
];

/** Админ-раздел доступен только владельцу (is_owner). */
export function AdminLayout() {
  const { isOwner } = useAuth();
  if (!isOwner) return <Navigate to="/" replace />;

  return (
    <div>
      <nav className="subtabs">
        {SUBTABS.map((t) => (
          <NavLink
            key={t.to}
            to={t.to}
            end={t.end}
            className={({ isActive }) => "subtab" + (isActive ? " active" : "")}
          >
            {t.label}
          </NavLink>
        ))}
      </nav>
      <Outlet />
    </div>
  );
}
