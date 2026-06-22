/** Axios instance with default config. */

import axios from 'axios'

const client = axios.create({
  baseURL: '/api/v1',
  timeout: 60_000,
  headers: { 'Content-Type': 'application/json' },
})

export default client
