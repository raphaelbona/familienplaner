import { apiFetch } from "./client";

export type FamilyMember = {
  id: string;
  display_name: string;
  avatar_key: string | null;
  likes: string[];
  dislikes: string[];
  allergies: string[];
};

type MemberPreferences = {
  user_id: string;
  display_name: string;
  likes: string[];
  dislikes: string[];
  allergies: string[];
};

type ChildAvatar = { id: string; avatar_key: string | null };

/** All family members (parents only) including their food preferences; avatars come from the child list. */
export async function fetchFamilyMembers(): Promise<FamilyMember[]> {
  const [members, children] = await Promise.all([
    apiFetch<MemberPreferences[]>("/food-preferences/"),
    apiFetch<ChildAvatar[]>("/families/children").catch(() => [] as ChildAvatar[])
  ]);
  const avatars = new Map(children.map((c) => [c.id, c.avatar_key]));
  return members.map((m) => ({
    id: m.user_id,
    display_name: m.display_name,
    avatar_key: avatars.get(m.user_id) ?? null,
    likes: m.likes,
    dislikes: m.dislikes,
    allergies: m.allergies
  }));
}
