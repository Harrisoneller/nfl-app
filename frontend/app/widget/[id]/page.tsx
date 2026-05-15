import { api } from "@/lib/api";
import { Card } from "@/components/Card";
import { WidgetRenderer } from "@/components/widgets/WidgetRenderer";

export default async function WidgetPage({ params }: { params: { id: string } }) {
  const widgets = await api.listWidgets().catch(() => []);
  const w = widgets.find((x) => x.id === params.id);
  if (!w) return <p>Widget not found.</p>;
  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-semibold">{w.title}</h1>
      <WidgetRenderer spec={w.spec as any} />
    </div>
  );
}
