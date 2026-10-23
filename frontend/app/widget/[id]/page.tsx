import { api } from "@/lib/api";
import { WidgetRenderer } from "@/components/widgets/WidgetRenderer";

export default async function WidgetPage({ params }: { params: { id: string } }) {
  // Public read: this page is server-rendered, and getStoredToken() is null
  // here, so the authenticated list/render routes 401 in multi-user mode.
  const view = await api.getPublicWidget(params.id).catch(() => null);
  if (!view) return <p>Widget not found.</p>;
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-semibold">{view.widget.title}</h1>
      <WidgetRenderer spec={view.widget.spec as any} data={view.data} />
    </div>
  );
}
