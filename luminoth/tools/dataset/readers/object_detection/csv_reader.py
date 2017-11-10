import os
import tensorflow as tf
import csv

from PIL import Image

from luminoth.tools.dataset.readers import InvalidDataDirectory
from luminoth.tools.dataset.readers.object_detection import (
    ObjectDetectionReader
)
from luminoth.utils.config import is_basestring
from luminoth.utils.dataset import read_image


# We automatically map common key names to known values.
# Feel free to add new values commonly found in the wild.
FIELD_MAPPER = {
    'x_min': 'xmin',
    'x1': 'xmin',
    'x_max': 'xmax',
    'x2': 'xmax',
    'y_min': 'ymin',
    'y1': 'ymin',
    'y_max': 'ymax',
    'y2': 'ymax',
    'class_id': 'label',
    'class': 'label',
    'label_id': 'label',
    'img_name': 'image_id',
    'img_id': 'image_id',
    'image': 'image_id',
}

DEFAULT_COLUMNS = [
    'image_id', 'xmin', 'ymin', 'xmax', 'ymax', 'label'
]


class CSVReader(ObjectDetectionReader):
    def __init__(self, data_dir, split, columns=DEFAULT_COLUMNS,
                 with_header=False, **kwargs):
        super(CSVReader, self).__init__()
        self._data_dir = data_dir
        self._split = split
        self._labels_filename = self._get_labels_filename()

        if is_basestring(columns):
            columns = columns.split(',')
        self._columns = columns
        self._with_header = with_header

        self._total = None
        self._classes = None
        self._files = None

        self.errors = 0
        self.yielded_records = 0

    @property
    def total(self):
        if self._total is None:
            self._total = len(self._get_records())
        return self._total

    @property
    def classes(self):
        if self._classes is None:
            self._classes = sorted(set([
                g['label']
                for r in self._get_records().values()
                for g in r
            ]))
        return self._classes

    @property
    def files(self):
        if self._files is None:
            files_dir = os.path.join(self._data_dir, self._split)
            self._files = tf.gfile.ListDirectory(files_dir)
        return self._files

    def iterate(self):
        records = self._get_records()
        for image_id, image_data in records.items():

            image_path = self._get_image_path(image_id)
            if image_path is None:
                tf.logging.debug(
                    'Could not find image_path for image "{}".'.format(
                        image_id
                    ))
                self.errors += 1
                continue

            try:
                image = read_image(image_path)
            except tf.errors.NotFoundError:
                tf.logging.debug('Could not find image "{}" in "{}".'.format(
                    image_id, image_path
                ))
                self.errors += 1
                continue

            image_pil = Image.open(image_path)
            width = image_pil.width
            height = image_pil.height
            image_pil.close()

            gt_boxes = []
            for b in image_data:
                try:
                    label_id = self.classes.index(b['label'])
                except ValueError:
                    tf.logging.debug('Error finding id for label "{}".'.format(
                        b['label']
                    ))
                    continue

                gt_boxes.append({
                    'label': label_id,
                    'xmin': b['xmin'],
                    'ymin': b['ymin'],
                    'xmax': b['xmax'],
                    'ymax': b['ymax'],
                })

            if len(gt_boxes) == 0:
                continue

            self.yielded_records += 1

            yield {
                'width': width,
                'height': height,
                'depth': 3,
                'filename': image_id,
                'image_raw': image,
                'gt_boxes': gt_boxes,
            }

    def _get_records(self):
        label_file = tf.gfile.Open(self._labels_filename)
        csv_reader = csv.DictReader(label_file, fieldnames=self._columns)

        images_gt_boxes = {}

        first = True
        for csv_line in csv_reader:
            if first and self._with_header:
                first = False
                continue

            csv_line = dict(csv_line)
            label_dict = self._normalize_csv_line(csv_line)

            image_id = label_dict.pop('image_id')
            images_gt_boxes.setdefault(image_id, []).append(label_dict)

        return images_gt_boxes

    def _normalize_csv_line(self, line_dict):
        line_dict = line_dict.copy()

        # Map known key names to known values.
        for old_key, new_key in FIELD_MAPPER.items():
            if old_key in line_dict:
                line_dict[new_key] = line_dict.pop(old_key)

        # Remove invalid/unknown keys
        valid_keys = set(FIELD_MAPPER.values())
        line_keys = line_dict.keys()
        for key in line_keys:
            if key not in valid_keys:
                line_dict.pop(key)

        if set(line_dict.keys()) != set(DEFAULT_COLUMNS):
            raise InvalidDataDirectory('Missing keys from CSV')

        return line_dict

    def _get_image_path(self, image_id):
        default_path = os.path.join(self._data_dir, self._split, image_id)
        if tf.gfile.Exists(default_path):
            return default_path

        # Lets assume it doesn't have extension
        possible_files = [
            f for f in self.files if f.startswith('{}.'.format(image_id))
        ]
        if len(possible_files) == 0:
            return
        elif len(possible_files) > 1:
            tf.logging.warning(
                'Image {} matches with {} files ({}).'.format(
                    image_id, len(possible_files)))

        return os.path.join(self._data_dir, self._split, possible_files[0])

    def _get_labels_filename(self):
        """Get the label file.
        """
        root_labels = os.path.join(
            self._data_dir, '{}.csv'.format(self._split)
        )

        if tf.gfile.Exists(root_labels):
            return root_labels

        split_labels_generic = os.path.join(
            self._data_dir, self._split, 'labels.csv'
        )

        if tf.gfile.Exists(split_labels_generic):
            return split_labels_generic

        split_labels_redundant = os.path.join(
            self._data_dir, self._split, '{}.csv'.format(self._split)
        )

        if tf.gfile.Exists(split_labels_redundant):
            return split_labels_redundant

        raise InvalidDataDirectory(
            'Could not find labels for "{}" in "{}"'.format(
                self._split, self._data_dir
            ))