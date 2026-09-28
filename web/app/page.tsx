import { redirect } from "next/navigation";
import { apiMode } from "@/lib/api/client";

export default function HomePage() {
  redirect(apiMode === "live" ? "/predictions" : "/analyze");
}
