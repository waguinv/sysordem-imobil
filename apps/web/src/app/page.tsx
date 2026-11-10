import { redirect } from "next/navigation";
import { getToken } from "@/lib/session";

export default async function Home() {
  redirect((await getToken()) ? "/app" : "/entrar");
}
