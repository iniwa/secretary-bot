/** /api/generation/* への薄いラッパー。*/
import { api } from '../api.js';

export const GenerationAPI = {
  // Jobs
  submit(body) {
    return api('/api/generation/submit', { method: 'POST', body });
  },
  listJobs({ status = null, limit = 50, offset = 0, modality = 'image' } = {}) {
    const params = { limit, offset, modality };
    if (status) params.status = status;
    return api('/api/generation/jobs', { params });
  },
  getJob(jobId) {
    return api(`/api/generation/jobs/${encodeURIComponent(jobId)}`);
  },
  cancelJob(jobId) {
    return api(`/api/generation/jobs/${encodeURIComponent(jobId)}/cancel`, {
      method: 'POST',
    });
  },
  gallery({ limit = 50, offset = 0, favorite = false, tag = null } = {}) {
    const params = { limit, offset };
    if (favorite) params.favorite = 1;
    if (tag) params.tag = tag;
    return api('/api/generation/gallery', { params });
  },
  galleryTags() {
    return api('/api/generation/gallery/tags');
  },
  setJobFavorite(jobId, favorite) {
    return api(`/api/generation/jobs/${encodeURIComponent(jobId)}/favorite`, {
      method: 'PATCH', body: { favorite },
    });
  },
  setJobTags(jobId, tags) {
    return api(`/api/generation/jobs/${encodeURIComponent(jobId)}/tags`, {
      method: 'PATCH', body: { tags },
    });
  },

  // Workflows (既存 /api/image/workflows を流用)
  listWorkflows() {
    return api('/api/image/workflows');
  },

  // Section categories
  listCategories() {
    return api('/api/generation/section-categories');
  },
  createCategory(body) {
    return api('/api/generation/section-categories', { method: 'POST', body });
  },
  updateCategory(key, body) {
    return api(`/api/generation/section-categories/${encodeURIComponent(key)}`, {
      method: 'PATCH', body,
    });
  },
  deleteCategory(key) {
    return api(`/api/generation/section-categories/${encodeURIComponent(key)}`, {
      method: 'DELETE',
    });
  },

  // Sections
  listSections({ categoryKey = null, starredOnly = false } = {}) {
    const params = {};
    if (categoryKey) params.category_key = categoryKey;
    if (starredOnly) params.starred_only = 'true';
    return api('/api/generation/sections', { params });
  },
  getSection(id) {
    return api(`/api/generation/sections/${id}`);
  },
  createSection(body) {
    return api('/api/generation/sections', { method: 'POST', body });
  },
  updateSection(id, body) {
    return api(`/api/generation/sections/${id}`, { method: 'PATCH', body });
  },
  deleteSection(id) {
    return api(`/api/generation/sections/${id}`, { method: 'DELETE' });
  },

  // Compose preview（サーバ検証用）
  composePreview(body) {
    return api('/api/generation/compose-preview', { method: 'POST', body });
  },
};
