'''
Created on Aug 7, 2018

@author: dwrigley

resolve links for External Database/Schema objects
write a custom lineage file to import the links
or
options include to create links directly (via API)
    (currently commented out - needs more testing + custom lineage is the better way)

Note:  the code is still a work in progress
       use/test at your own risk 
       (& contribute back if you make it better)

'''
import requests
import json
from requests.auth import HTTPBasicAuth
import csv
import platform
import edcutils
import time
import sys
# import logging


# ******************************************************
# change these settings for your catalog service
# ******************************************************
catalogServer='http://napslxapp01:9085'
#catalogServer='http://18.236.138.21:9085'
url = catalogServer + '/access/2/catalog/data/objects'
header = {"Accept": "application/json"} 
parameters = {'q': 'core.classType:com.infa.ldm.relational.ExternalDatabase' 
              , 'offset': 0
              , 'pageSize': 1000}
uid='Administrator'
pwd='Administrator'

# alternate for finding links for a specific resource/db
# core.classType:com.infa.ldm.relational.ExternalDatabase and core.resourceName:SybDMSDev_dmproddb and core.name:External

# the csv lineage file to write to
csvFileName="dbms_externalDBLinks.csv"
csvFilePath="out/"


# resource import settings (used to create|update/upload file/start resource load)
executeEDCImport=False
lineageResourceName="externalDBlinker_lineage"
lineageResourceTemplate="template/custom_lineage_template.json"
waitToComplete=False

#csvFileName = "out/ABSQL_externalDBLinks.csv"

# ******************************************************
# end of parameters that should be changed 
# ******************************************************
outputFile=csvFilePath + csvFileName


def getColumnsForTable(tableId):
    """
    get all columns for a table and return in a dict key=column name (lowercase) val=column id
    use the GET /access/2/catalog/data/objects id=tableId
    look for columns via com.infa.ldm.relational.TableColumn 
          or com.infa.ldm.relational.ViewViewColumn relationship type
    
    """
    #TODO: this could be moved to edcutils.py

    print("\t\t\tget cols for table:" + tableId)
    parameters = {'id': tableId, 'offset': 0, 'pageSize': 1}    # pagesize can be 1 - since we are only passing the id
    colResp = requests.get(url,params=parameters,headers=header, auth=HTTPBasicAuth(uid,pwd))
    tableJson = colResp.json()
    #print(colResp.status_code)
    colIds=dict()
    for extDBItem in tableJson["items"]:
        #print(">>>")
        for dst in extDBItem["dstLinks"]:
            assoc=dst.get("association")
            colId=dst.get("id")
            colName=dst.get('name')
            #print("\tdst..." + assoc)
            if assoc in ('com.infa.ldm.relational.TableColumn', 'com.infa.ldm.relational.ViewViewColumn'):
                # key = lower_case name - for case-insensitive lookup
                colIds[colName.lower()] = colId
                
    return colIds

def processExternalDB(dbId, classType, dbName, resType, resName, colWriter):
    '''
    dbId = the id of the external database object
    classType = the classtype of the external database oject
    dbNAme - the name of the external db object
    resTYpe - the resource type e.g Oracle, SQLServer that created the external db object
    resName - the resource name that created the external db object
    colWriter - the csv File object to write any lineage entries

    process outline:-
    get lineage to the table level (2 parent/child hops) from the external database
        for each table, find the actual table that should be linked (case insensitive)
        if only 1 table was found (otherwise - print an error message) 
            link the table and all columns
    '''

    dbUnknown = False
    # counters for # of links created
    tabLinks = 0
    colLinks = 0
    errors = 0
    
    # 'External' can be used if the database name is not known (sqlserver use case)
    if dbName=='External':
        dbUnknown=True
        print("\tthe database for the externally referenced object is unknown")
        
    # note:  if the database type is oracle, we can't trust the name of the database
    #        since it will contain the dblink name, not the externally referenced database
    #        so we will treat it as unknown and just try to find the schema/table referenced
    if resType=='Oracle':
        dbUnknown=True
            
    print('\ttype=' + classType + " name=" + dbName + " id=" + dbId + " unknownDB:" + str(dbUnknown))
    
    # get the lineage for the database object
    lineageURL=catalogServer + '/access/2/catalog/data/relationships'
    lineageParms={"seed": dbId
              , "association": "core.ParentChild"
              , "depth": "2"
              , "direction": "OUT"
              , "includeAttribute": {'core.name', 'core.classType'}
              , "includeTerms": "false"
              , "removeDuplicateAggregateLinks": "false"
              }
    print("\tLineage query for: " + dbName + "  params=" + str(lineageParms))
    lineageResp = requests.get(lineageURL,params=lineageParms,headers=header, auth=HTTPBasicAuth(uid,pwd))
#    lineageResp = requests.get(test,headers=header, auth=HTTPBasicAuth('Administrator','Administrator'))
    lineageStatus = lineageResp.status_code
    print("\tlineage rc=" + str(lineageStatus))
    lineageJson = lineageResp.text
     
    #  bug in the relationships api call - the items collection sould be "items" (with quotes)
    if lineageJson.startswith('{items'):
        # bug in 10.2.1 and before
        # replace items with "items" - for just the first occurrence (note:  should be fixed after 10.2.1
        lineageJson = json.loads(lineageJson.replace('items', '"items"', 1))
    else:
        # 10.2.1u1 + json is escaped properly
        lineageJson = json.loads(lineageJson)
        

    # for each item in the lineage resultset        
    for lineageItem in lineageJson["items"]:
        inId = lineageItem.get("inId")
        assocId = lineageItem.get("associationId")
        schemaName = ''
        #print("\t" + inId + " assoc=" + assocId)
        if assocId=='com.infa.ldm.relational.ExternalSchemaTable':
            # find the table...
            schemaName = inId.split('/')[-2]    
            inEmbedded = lineageItem.get("inEmbedded")
            #print('\tprocessing schema=' + schemaName + ' db=' + dbName + " id=" + inId)
            tableName=edcutils.getFactValue(inEmbedded, "core.name")                
            print('\tprocessing table=' + tableName  + ' schema=' + schemaName + ' db=' + dbName + " id=" + inId)
            
            # format the query to find the actual table
            q="core.classType:(com.infa.ldm.relational.Table or com.infa.ldm.relational.View)  and core.name_lc_exact:" + tableName
            if dbUnknown and schemaName=='':
                q=q+ ' and core.resourceName:' + resName 
            #if dbUnknown==False:
            #    q=q+ ' and ' + dbName
            q=q+ ' and core.resourceType:"' + resType + '"'
            tableSearchParms={'q': q, 'offset': 0, 'pageSize': 100}
            print("\t\tquery=" + str(tableSearchParms))
            # find the table - with name tableName
            tResp = requests.get(url,params=tableSearchParms,headers=header, auth=HTTPBasicAuth(uid,pwd))
            tStatus = tResp.status_code
            print("\t\tquery rc=" + str(tStatus))
            #print("\t\t\t\t" + str(tResp.json()))
            foundTabId=''
            tableMatchCount=0
            # possible matching tables
            for tableItem in tResp.json()["items"]:
                fromTableId = tableItem["id"]
                fromSchemaName = fromTableId.split('/')[-2].lower()
                #foundTabId=''
                #foundTabId = tableItem["id"]
                #print("rs-" + fromTableId + " compared with " + inId);
                if fromTableId != inId and fromTableId.count('/')==4:        # filter out comparing against itself + counting the / chars - 4 = normal db, 5=external db (disregard here)
                    print('\t\tchecking ' + fromTableId)
                    #print("could be this one...." + foundTabId + " << " + inId)
                    theName = fromTableId.split('/')[-1];
                    '''
                    # debug statements here
                    print("debug theName=" + theName);
                    print("debug tableName=" + tableName);
                    print("debug schemaName=" + schemaName);
                    print("debug fromSchemaName=" + fromSchemaName);
                    print("debug fromSchemaName=" + fromSchemaName.lower());
                    print("check1:" + str(theName.lower()==tableName.lower()) );
                    print("check2:" + str(schemaName.lower()==fromSchemaName.lower()) );
                    print("check2.5:" + str(schemaName=="") );                    
                    print("check3:" + str(fromSchemaName=="dbo"));
                    print("check4:" + str(schemaName=="" and fromSchemaName=="dbo"));
                    print("check5:" + str(schemaName.lower()==fromSchemaName.lower() or ( schemaName=="" and fromSchemaName=="dbo") ) ) ;
                    '''
                    
                     # the schema could be empyt - in that case it should match dbo - unless we have a way of knowing what the default schema is
                    if theName.lower()==tableName.lower() and (schemaName.lower()==fromSchemaName.lower() or ( schemaName=="" and fromSchemaName=="dbo") ):
                        tableMatchCount += 1
                        foundTabId = fromTableId
                        print("\t\ttable name matches..." + theName + "==" + tableName + " " + inId + " " + foundTabId + " count/" + str(fromTableId.count('/')))
                    else:
                        print("\t\tno match...schema match 1:" + theName + " 2:" + tableName + " 3:" + schemaName + " 4:" + fromSchemaName + ":" + str(schemaName.lower()==fromSchemaName.lower()))
                        #print("\t\tno match found: schema match(" + schemaName + ")?==" + schemaName.lower()==fromSchemaName.lower())
                #else:
                    #print("skipping this one");
            
            print("\t\ttotal matching tables=" + str(tableMatchCount) + " inId:" + inId)
            if tableMatchCount==1:
                # ok - we have a single table to match
                # link at the table level - then get the columns and link them too
                #print("linking from actual tab - to external ref tab")
                print("\t\tlinking tables " + foundTabId + ' -->> ' + inId)
                # get the columns for the ext Table (will be a reduced set - only the linked columns)
                extCols = getColumnsForTable(inId)
                tabCols = getColumnsForTable(foundTabId)
                #print("\t\t\text cols:" + str(len(extCols))  + ' tablCols:' + str(len(tabCols)))
                
                # link the table level
                edcutils.exportLineageLink(foundTabId, inId, 'core.DataSetDataFlow', colWriter)
                tabLinks += 1
                
                tabColsLinked=0
                    
                # match the columns on the left/right side
                for toCol, toId in extCols.items():
                    # check if the toCol (the name) exists in the tabCols dict
                    #print('\t\t\tchecking toCol:' + toCol + tabCols.get(toCol))
                    fromId = tabCols.get(toCol)
                    if fromId != None:
                        #print('\t\t\tlinking columns...' + fromId + ' --->>>' + toId)
                        edcutils.exportLineageLink(fromId, toId, 'core.DirectionalDataFlow', colWriter)
                        tabColsLinked += 1
                        colLinks += 1
                    else:
                        print("\t\t\tError: cannot find column " + toCol + " in table " + inId)
                        errors +=1
                print("\t\t\text cols:" + str(len(extCols))  + ' tablCols:' + str(len(tabCols)) + " linked=" + str(tabColsLinked))
                #print("\t\t\tcolumns linked=" + str(tabColsLinked))
            else:
                print("\t\tmutlple possible matches found (" + str(tableMatchCount) + ") no links will be created")
            # flush the console buffer - for tailing the stdout log
            sys.stdout.flush()
                
    print("external database: " + dbName + " processed: tab/col links created: " + str(tabLinks) + "/" + str(colLinks) + " errors:" + str(errors))
    print("")
    return tabLinks, colLinks, errors



def main():
    '''
    main starts here - run the query processing all items
    
    - find all com.infa.ldm.relational.ExternalDatabase objects
      - for each - call processExternalDB
    '''
    print ("ExternalDBLinker started")
    start_time = time.time()

    tableLinksCreated=0
    columnLinksCreated=0
    errorsFound=0
    
    '''
    logger = logging.getLogger('externalDBLinker')
    logger.setLevel('DEBUG')

    file_log_handler = logging.FileHandler('logfile.log')
    logger.addHandler(file_log_handler)
    
    stderr_log_handler = logging.StreamHandler()
    logger.addHandler(stderr_log_handler)
    
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_log_handler.setFormatter(formatter)
    stderr_log_handler.setFormatter(formatter)
    
    logger.info('Info message')
    logger.error('Error message')
    '''
    
    columnHeader=["Association", "From Connection","To Connection","From Object","To Object"]
    if str(platform.python_version()).startswith("2.7"):
        fCSVFile = open(outputFile,"w")
    else:
        fCSVFile = open(outputFile,"w", newline='', encoding='utf-8')
    print("custom lineage file initialized. " + outputFile)
    colWriter=csv.writer(fCSVFile)
    colWriter.writerow(columnHeader)
    
    print("executing query to find all external DB objects: " + url + ' q=' + parameters.get('q'))
    resp = requests.get(url,params=parameters,headers=header, auth=HTTPBasicAuth(uid,pwd))
    status = resp.status_code
    print ('extDB query rc=' + str(status))
    
    resultJson = resp.json()
    total=resultJson['metadata']['totalCount']
    print("external db objects found... " + str(total))
    currentDB = 0
    
    # for each externalDatabase object 
    for extDBItem in resultJson["items"]:
        itemId=extDBItem["id"]
        currentDB += 1
        print("processing database: " + itemId + " " + str(currentDB) + ' of ' + str(total))
        itemType = edcutils.getFactValue(extDBItem, "core.classType")
        itemName = edcutils.getFactValue(extDBItem, "core.name")
        resourceName = edcutils.getFactValue(extDBItem, "core.resourceName")
        resourceType = edcutils.getFactValue(extDBItem, "core.resourceType")
        
        tabLinks, colLinks, errors = processExternalDB(itemId, itemType, itemName, resourceType, resourceName, colWriter)
        tableLinksCreated += tabLinks
        columnLinksCreated += colLinks
        errorsFound += errors
        
        sys.stdout.flush() 
                
    
    fCSVFile.close
    print("finished!")
    print("table links:   created=" + str(tableLinksCreated))
    print("column links:  created=" + str(columnLinksCreated))
    print("errors found: " + str(errorsFound))

    # end of main()
    print("Finished - run time = %s seconds ---" % (time.time() - start_time))
    
    # call the resource create/update/load function to get the data imported into EDC
    if executeEDCImport:
        edcutils.createOrUpdateAndExecuteResource(catalogServer, uid, pwd, lineageResourceName, lineageResourceTemplate, csvFileName, outputFile, waitToComplete)


# call main - if not already called or used by another script 
if __name__== "__main__":
    main()            

