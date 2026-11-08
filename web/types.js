/** @typedef {{page:number, excerpt:string}} Evidence */
/** @typedef {{value:string|null, evidence:Evidence|null}} Fact */
/** @typedef {{document_type:string, complete:boolean, issues:string[], invoices:Record<string,Fact|string>[], transactions:Record<string,Fact|string>[]}} Accounting */
/** @typedef {{id:string,name:string,status:'queued'|'processing'|'ready'|'failed',stage:string,size:number,page_count:number,ocr_pages:number,error:string|null,media_type:string,accounting:Accounting|null}} DocumentRecord */
/** @typedef {{id:string,document_id:string,document_name:string,page:number,excerpt:string}} Source */
/** @typedef {{text:string,source_ids:string[]}} Claim */
/** @typedef {{label:string,amount:string,currency:string,source_ids:string[]}} Calculation */
/** @typedef {{status:'answered'|'insufficient_information'|'clarification_needed',title:string,claims:Claim[],sources:Source[],calculations:Calculation[],issues:string[],as_of:string,coverage:string}} Answer */
/** @typedef {{role:'user'|'assistant',content:string}} Message */
/** @typedef {{model:string,model_configured:boolean,ocr_installed:boolean,max_file_bytes:number,max_pages:number}} Status */
export {};
