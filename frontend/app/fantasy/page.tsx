import { redirect } from "next/navigation";

/**
 * The fantasy hub moved into the Players zone (one projection engine, one
 * page). This route survives so old links and bookmarks keep working.
 */
export default function FantasyRedirect() {
  redirect("/players?tab=fantasy");
}
