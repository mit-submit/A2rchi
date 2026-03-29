from src.data_manager.collectors.scrapers.adapters import to_scraped_resource

class AdapterPipeline:
    def process_item(self, item, spider):
        resource = to_scraped_resource(item)
        # Implicitly, set site for every pair of spider/resource.
        resource.metadata["site"] = spider.name
        return item
