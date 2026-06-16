import { Segmented } from "./ui/Segmented";

/**
 * Season picker. Renders the available seasons as a horizontal segmented strip.
 */
export function SeasonSelect({
  seasons,
  value,
  onChange,
}: {
  seasons: number[];
  value: number;
  onChange: (s: number) => void;
}) {
  const options = seasons.map((s) => ({ id: String(s), label: String(s) }));
  return (
    <Segmented
      options={options}
      value={String(value)}
      onChange={(id) => onChange(Number(id))}
    />
  );
}
