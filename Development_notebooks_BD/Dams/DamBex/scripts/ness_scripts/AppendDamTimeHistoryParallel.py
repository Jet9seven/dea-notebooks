# # Get dam time history - parallel workflow
# 
# **What this notebook does** This notebook uses the python module `multiprocessing` to simply parallelise a workflow. Here we have a shapefile containing polygons we wish to explore. Each polygon is independant from the other, and so lends itself to simple parallelisation of the workflow. 
# 
# This code was parallelised by moving all of the processing into a single function (here called `FindOutHowFullTheDamIs`). This function is called by the `multiprocessing` module inside a loop. The code loops through each polygon in the shapefile, and assigns it to an available CPU for processing. Once that polygon has been processed, the CPU moves on to the next one until all of the polygons have been processed. This all runs as a background process, so the notebook appears to not be doing anything when the code is running.
# 
# **Required inputs** Shapefile containing the polygon set of water bodies to be interrogated.
# 
# **Date** August 2018
# 
# **Author** Claire Krause, Jono Mettes

# In[1]:


from datacube import Datacube
from datacube.utils import geometry
from datacube.storage import masking
import fiona
import rasterio.features
import numpy as np
import csv
import multiprocessing
import sys
from math import ceil
from datetime import datetime

def find_lastline(fname, linenum = -1):
    with open(fname, 'r') as f:
        lines = f.read().splitlines()
        last_line = lines[linenum]
        return last_line

# ## Set up some file paths

shape_file = '/g/data/r78/cek156/dea-notebooks/Dams/Dams2000to2018/AllNSW2000to201810pcMinMaxRiverCleaned.shp'
print(f'Reading in {shape_file}')

lastDateLog = '/g/data/r78/vmmn547/Dams/lastTimeLog.txt'
lastTime = find_lastline(lastDateLog)
currentTime = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
part = sys.argv[1]
part = int(part)
print(f'Working on chunk {part}')

global Output_dir
Output_dir = '/g/data/r78/vmn547/Dams/Timeseries_new2'

# ## Loop through the polygons and write out a csv of dam capacity
import time

# Get the shapefile's crs
with fiona.open(shape_file) as shapes:
    crs = geometry.CRS(shapes.crs_wkt) 
    ShapesList = list(shapes)
    ChunkSize = ceil(len(ShapesList)/32) + 1
    shapessubset = shapes[(part - 1) * ChunkSize: part * ChunkSize]
print(f'The index we will use is {(part - 1) * ChunkSize, part * ChunkSize}')


# Define a function that does all of the work    
def FindOutHowFullTheDamIs(shapes, crs):
    """
    This is where the code processing is actually done. This code takes in a polygon, and the
    shapefile's crs and performs a polygon drill into the wofs_albers product. The resulting 
    xarray, which contains the water classified pixels for that polygon over every available 
    timestep, is used to calculate the percentage of the water body that is wet at each time step. 
    The outputs are written to a csv file named using the polygon ID. 
    
    Inputs:
    shapes - polygon to be interrogated
    crs - crs of the shapefile
    
    Outputs:
    Nothing is returned from the function, but a csv file is written out to disk   
    """
    dc = Datacube(app = 'Polygon drill')
    first_geometry = shapes['geometry']
    polyName = shapes['properties']['ID']
    print(polyName)
    polyArea = shapes['properties']['area']
    geom = geometry.Geometry(first_geometry, crs=crs)

    fname = '{0}/{1}.txt'.format(Output_dir, polyName)
    last_date, lastObs = find_lastline(fname, linenum=-1).split(',')
    ## Set up the query, and load in all of the WOFS layers
    from_date = datetime.strptime(last_date, '%Y-%m-%d %H:%M:%S.%f')
    query = {'geopolygon': geom}
    WOFL = dc.load(product='wofs_albers', **query)

    # Make a mask based on the polygon (to remove extra data outside of the polygon)
    mask = rasterio.features.geometry_mask([geom.to_crs(WOFL.geobox.crs) for geoms in [geom]],
                                           out_shape=WOFL.geobox.shape,
                                           transform=WOFL.geobox.affine,
                                           all_touched=False,
                                           invert=True)
    ## Work out how full the dam is at every time step
    DamCapacityPc = []
    DamCapacityCt = []
    DryObserved = []
    InvalidObservations = []
    for ix, times in enumerate(WOFL.time):
        # Grab the data for our timestep
        AllTheBitFlags = WOFL.water.isel(time = ix)
        # Find all the wet/dry pixels for that timestep
        WetPixels = masking.make_mask(AllTheBitFlags, wet=True)
        DryPixels = masking.make_mask(AllTheBitFlags, dry=True)
        # Apply the mask and count the number of observations
        MaskedAll = AllTheBitFlags.where(mask).count().item()
        MaskedWet = WetPixels.where(mask).sum().item()
        MaskedDry = DryPixels.where(mask).sum().item()
        # Turn our counts into percents
        try:
            WaterPercent = MaskedWet / MaskedAll * 100
            DryPercent = MaskedDry / MaskedAll * 100
            UnknownPercent = (MaskedAll - (MaskedWet + MaskedDry)) / MaskedAll *100
        except ZeroDivisionError:
            WaterPercent = 0.0
            DryPercent = 0.0
            UnknownPercent = 100.0
        # Append the percentages to a list for each timestep
        DamCapacityPc.append(WaterPercent)
        InvalidObservations.append(UnknownPercent)
        DryObserved.append(DryPercent)
        DamCapacityCt.append(MaskedWet)

    ## Filter out timesteps with less than 90% valid observations 
    try:
        ValidMask = [i for i, x in enumerate(InvalidObservations) if x < 10]
        ValidObs = WOFL.time[ValidMask].dropna(dim = 'time')
        ValidCapacityPc = [DamCapacityPc[i] for i in ValidMask]
        ValidCapacityCt = [DamCapacityCt[i] for i in ValidMask]

        DateList = ValidObs.to_dataframe().to_csv(None, header=False, index=False).split('\n')
        rows = zip(DateList,ValidCapacityCt,ValidCapacityPc)

        if DateList:
            with open('{0}/{1}.txt'.format(Output_dir, polyName), 'a') as f:
                writer = csv.writer(f)
                for row in rows:
                    writer.writerow(row)
    except:
        print(f'This polygon isn\'t working...: {polyName}')

#-----------------------------------------------------------------------#

# Here is where the parallelisation actually happens...                
# p = multiprocessing.Pool()

# Launch a process for each polygon.
# The result will be approximately one process per CPU core available.
for shapes in shapessubset:
    FindOutHowFullTheDamIs(shapes, crs)
