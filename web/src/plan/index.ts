/**
 * Public surface of the plan review package (T027).
 *
 * Like `../map/index.ts`, this barrel is what an app entry point imports. It re-exports
 * only this package's own modules — nothing from `../travel`, whose barrel pulls
 * `geojson-path-finder` and the OPFS bundle reader in behind it and which the planning
 * phase has no use for. `./parse` deep-imports the one narrowing function it reuses.
 */

export {
  DEFAULT_PLANS_ENDPOINT,
  PlanApprovalConflictError,
  PlanRequestError,
  PlanStreamError,
  approvePath,
  approvePlan,
  fetchPlan,
  planPath,
  streamPlan,
  type PlanFetchOptions,
  type PlanHandlers,
  type PlanProposal,
  type StreamPlanOptions,
} from './client'
export {
  DEFAULT_DAY_START,
  DEFAULT_HOURS,
  DEFAULT_WALKING_M,
  PlanRequestForm,
  mountPlanForm,
  validatePlanRequest,
  type PlanFormOptions,
  type PlanFormProblem,
  type PlanFormResult,
  type PlanFormValues,
} from './form'
export {
  planState,
  readItineraryFrame,
  sanitiseApproval,
  sanitiseApproveResult,
  sanitiseFeasibility,
  sanitisePlanDetail,
  sanitisePlanDone,
  sanitisePlanStatus,
} from './parse'
export {
  EMPTY_PLAN_MODEL,
  PlanReviewSurface,
  approvability,
  createPlanStructureCredit,
  createVerdictCaption,
  describeApprovalFailure,
  feasibilityPasses,
  hasWarnings,
  mountPlanReview,
  renderApprovalFailure,
  renderApproveControl,
  renderFeasibility,
  renderPlanLeg,
  renderPlanPanel,
  renderPlanStop,
  renderWarnings,
  type Approvability,
  type PlanApplyDetail,
  type PlanApprovalFailure,
  type PlanPanelOptions,
  type PlanReviewModel,
  type PlanReviewOptions,
} from './render'
export { PLAN_STATES } from './types'
export type * from './types'
