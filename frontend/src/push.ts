import { apiFetch } from "./api/client";

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = atob(base64);
  const output = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i++) {
    output[i] = rawData.charCodeAt(i);
  }
  return output;
}

export type SubscribeResult = "subscribed" | "unsupported" | "denied" | "unavailable";

export function isPushSupported(): boolean {
  return "serviceWorker" in navigator && "PushManager" in window;
}

export async function subscribeToPush(): Promise<SubscribeResult> {
  if (!isPushSupported()) return "unsupported";

  const { public_key: publicKey } = await apiFetch<{ public_key: string | null }>(
    "/notifications/vapid-public-key"
  );
  if (!publicKey) return "unavailable";

  const permission = await Notification.requestPermission();
  if (permission !== "granted") return "denied";

  const registration = await navigator.serviceWorker.ready;
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    // TS's lib.dom BufferSource type is overly strict about ArrayBuffer vs.
    // ArrayBufferLike here; a freshly constructed Uint8Array is safe to pass.
    applicationServerKey: urlBase64ToUint8Array(publicKey) as BufferSource
  });

  const json = subscription.toJSON();
  await apiFetch("/notifications/subscribe", {
    method: "POST",
    body: JSON.stringify({
      endpoint: json.endpoint,
      p256dh: json.keys?.p256dh,
      auth: json.keys?.auth
    })
  });

  return "subscribed";
}
