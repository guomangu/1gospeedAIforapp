# REALITY: harvester/src/core/queue-manager.ts (98 lignes)
- imports: `import type { BaseAdapter, JobPayload } from './base-adapter.js', import type { Job, JobsOptions } from 'bullmq', import { HttpError } from './http-client.js', import { Queue, Worker } from 'bullmq', import { createHash } from 'node:crypto', import { redisConnection } from '../config/redis.js'`
- `closeQueues`
- `dispatchNextJob`
- `getQueue`
- `jobId`
- `queueName`
- `startWorker`
- role: Existing module
