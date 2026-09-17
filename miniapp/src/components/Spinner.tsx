export function Spinner({ center = false }: { center?: boolean }) {
  if (center) {
    return (
      <div className="spinner-center">
        <div className="spinner" />
      </div>
    );
  }
  return <div className="spinner" />;
}
