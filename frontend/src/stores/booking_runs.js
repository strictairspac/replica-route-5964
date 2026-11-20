import {bookingRunsApi} from '../api/booking_runs.js';
export const bookingRunsStore={items:[],async refresh(){this.items=await bookingRunsApi.list();return this.items;}};
