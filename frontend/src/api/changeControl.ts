/** Change control (backend: orchestration/change_control.py, /api/admin/change):
 * workspaces, object locks, releases and the organization's root administrator. */
import { request } from "./client";

export interface WorkspaceObject {
  type: string;
  key: string;
  id: number;
  kind: string;
  label: string;
  revision: number;
  change: "new" | "changed" | "deleted";
}

export interface ChangeWorkspace {
  id: number;
  name: string;
  description: string;
  status: "open" | "submitted" | "released" | "discarded";
  owner_id: number;
  owner: string | null;
  created_at: string | null;
  updated_at: string | null;
  submitted_at: string | null;
  release_id: number | null;
  objects: WorkspaceObject[];
}

export interface ChangeRelease {
  id: number;
  number: number;
  name: string;
  status: "draft" | "deployed" | "rolled_back";
  workspaces: ChangeWorkspace[];
  map_version_id: number | null;
  created_by: string | null;
  created_at: string | null;
  deployed_by: string | null;
  deployed_at: string | null;
  rolled_back_at: string | null;
}

export interface LockInfo {
  user_id: number;
  user: string | null;
  workspace_id: number | null;
  workspace: string | null;
  since: string;
}

export interface ReleaseSimulation {
  ok: boolean;
  checks: { object: string; ok: boolean; message: string }[];
  problems: { object: string; ok: boolean; message: string }[];
  impact: { headline?: string } | null;
}

const BASE = "/api/admin/change";

export const changeMe = () => request<{ user_id: number; is_root: boolean; root_admin: { id: number; name: string } | null }>(`${BASE}/me`);
export const listWorkspaces = () => request<ChangeWorkspace[]>(`${BASE}/workspaces`);
export const createWorkspace = (name: string, description = "") =>
  request<ChangeWorkspace>(`${BASE}/workspaces`, { method: "POST", body: JSON.stringify({ name, description }) });
export const workspaceAction = (id: number, action: "submit" | "reopen" | "discard") =>
  request<ChangeWorkspace>(`${BASE}/workspaces/${id}/${action}`, { method: "POST" });
export const listReleases = () => request<ChangeRelease[]>(`${BASE}/releases`);
export const createRelease = (name: string, workspaceIds: number[], mapVersionId: number | null) =>
  request<ChangeRelease>(`${BASE}/releases`, { method: "POST", body: JSON.stringify({ name, workspace_ids: workspaceIds, map_version_id: mapVersionId }) });
export const simulateRelease = (id: number) => request<ReleaseSimulation>(`${BASE}/releases/${id}/simulate`, { method: "POST" });
export const releaseAction = (id: number, action: "deploy" | "rollback") => request<ChangeRelease>(`${BASE}/releases/${id}/${action}`, { method: "POST" });
export const breakLock = (objectType: string, objectKey: string) =>
  request<void>(`${BASE}/locks/break`, { method: "POST", body: JSON.stringify({ object_type: objectType, object_key: objectKey }) });
export const setRootAdmin = (userId: number) =>
  request<{ root_admin: { id: number; name: string } }>(`${BASE}/root-admin`, { method: "PUT", body: JSON.stringify({ user_id: userId }) });
