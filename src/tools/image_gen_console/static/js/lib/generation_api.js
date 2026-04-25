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
  gallery({
    limit = 60, offset = 0, favorite = false,
    tags = null, nsfw = false, q = '',
    workflow = null, collectionId = null,
    dateFrom = null, dateTo = null, order = 'new',
  } = {}) {
    const params = { limit, offset, order };
    if (favorite) params.favorite = 1;
    if (nsfw) params.nsfw = 1;
    if (q) params.q = q;
    if (workflow) params.workflow = workflow;
    if (collectionId) params.collection_id = collectionId;
    if (dateFrom) params.date_from = dateFrom;
    if (dateTo) params.date_to = dateTo;
    if (tags && tags.length) params.tags = tags.join(',');
    return api('/api/generation/gallery', { params });
  },
  galleryTags() {
    return api('/api/generation/gallery/tags');
  },
  gallerySimilar(jobId, limit = 20) {
    return api(`/api/generation/gallery/similar/${encodeURIComponent(jobId)}`, {
      params: { limit },
    });
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
  deleteJob(jobId, { keepFiles = false } = {}) {
    const qs = keepFiles ? '?keep_files=1' : '';
    return api(`/api/generation/jobs/${encodeURIComponent(jobId)}${qs}`, {
      method: 'DELETE',
    });
  },
  bulkDelete(jobIds, { keepFiles = false } = {}) {
    return api('/api/generation/jobs/bulk-delete', {
      method: 'POST', body: { job_ids: jobIds, keep_files: keepFiles },
    });
  },
  purgeJobs({ statuses = ['failed', 'cancelled'], modality = 'image' } = {}) {
    return api('/api/generation/jobs/purge', {
      method: 'POST', body: { statuses, modality },
    });
  },
  bulkFavorite(jobIds, favorite) {
    return api('/api/generation/jobs/bulk-favorite', {
      method: 'POST', body: { job_ids: jobIds, favorite },
    });
  },
  bulkTags(jobIds, tags, mode = 'add') {
    return api('/api/generation/jobs/bulk-tags', {
      method: 'POST', body: { job_ids: jobIds, tags, mode },
    });
  },

  // Collections
  listCollections() {
    return api('/api/generation/collections');
  },
  createCollection(body) {
    return api('/api/generation/collections', { method: 'POST', body });
  },
  updateCollection(id, body) {
    return api(`/api/generation/collections/${id}`, { method: 'PATCH', body });
  },
  deleteCollection(id) {
    return api(`/api/generation/collections/${id}`, { method: 'DELETE' });
  },
  addJobsToCollection(id, jobIds) {
    return api(`/api/generation/collections/${id}/jobs`, {
      method: 'POST', body: { job_ids: jobIds },
    });
  },
  removeJobsFromCollection(id, jobIds) {
    return api(`/api/generation/collections/${id}/jobs`, {
      method: 'DELETE', body: { job_ids: jobIds },
    });
  },

  // Workflows (既存 /api/image/workflows を流用)
  listWorkflows() {
    return api('/api/image/workflows');
  },
  workflowLoras(name) {
    return api(`/api/generation/workflows/${encodeURIComponent(name)}/loras`);
  },
  listCheckpoints() {
    return api('/api/generation/checkpoints');
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

  // Section presets（選択中セクション + ユーザー追記プロンプトのスナップショット）
  listSectionPresets({ nsfw = false } = {}) {
    const params = {};
    if (nsfw) params.nsfw = 1;
    return api('/api/generation/section-presets', { params });
  },
  createSectionPreset(body) {
    return api('/api/generation/section-presets', { method: 'POST', body });
  },
  updateSectionPreset(id, body) {
    return api(`/api/generation/section-presets/${id}`, { method: 'PATCH', body });
  },
  deleteSectionPreset(id) {
    return api(`/api/generation/section-presets/${id}`, { method: 'DELETE' });
  },

  // Compose preview（サーバ検証用）
  composePreview(body) {
    return api('/api/generation/compose-preview', { method: 'POST', body });
  },
};
