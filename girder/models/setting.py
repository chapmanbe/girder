#!/usr/bin/env python
# -*- coding: utf-8 -*-

###############################################################################
#  Copyright Kitware Inc.
#
#  Licensed under the Apache License, Version 2.0 ( the "License" );
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
###############################################################################

from collections import OrderedDict
import cherrypy
import pymongo
import six

from ..constants import SettingDefault, SettingKey
from .model_base import Model, ValidationException
from girder import logprint
from girder.utility import plugin_utilities, setting_utilities
from girder.utility.model_importer import ModelImporter
from bson.objectid import ObjectId


class Setting(Model):
    """
    This model represents server-wide configuration settings as key/value pairs.
    """
    def initialize(self):
        self.name = 'setting'
        # We had been asking for an index on key, like so:
        #   self.ensureIndices(['key'])
        # We really want the index to be unique, which could be done:
        #   self.ensureIndices([('key', {'unique': True})])
        # We can't do it here, as we have to update and correct older installs,
        # so this is handled in the reconnect method.

    def reconnect(self):
        """
        Reconnect to the database and rebuild indices if necessary.  If a
        unique index on key does not exist, make one, first discarding any
        extant index on key and removing duplicate keys if necessary.
        """
        super(Setting, self).reconnect()
        try:
            indices = self.collection.index_information()
        except pymongo.errors.OperationFailure:
            indices = []
        hasUniqueKeyIndex = False
        presentKeyIndices = []
        for index in indices:
            if indices[index]['key'][0][0] == 'key':
                if indices[index].get('unique'):
                    hasUniqueKeyIndex = True
                    break
                presentKeyIndices.append(index)
        if not hasUniqueKeyIndex:
            for index in presentKeyIndices:
                self.collection.drop_index(index)
            duplicates = self.collection.aggregate([{
                '$group': {'_id': '$key',
                           'key': {'$first': '$key'},
                           'ids': {'$addToSet': '$_id'},
                           'count': {'$sum': 1}}}, {
                '$match': {'count': {'$gt': 1}}}])
            for duplicate in duplicates:
                logprint.warning(
                    'Removing duplicate setting with key %s.' % (
                        duplicate['key']))
                # Remove all of the duplicates.  Keep the item with the lowest
                # id in Mongo.
                for duplicateId in sorted(duplicate['ids'])[1:]:
                    self.collection.delete_one({'_id': duplicateId})
            self.collection.create_index('key', unique=True)

    def validate(self, doc):
        """
        This method is in charge of validating that the setting key is a valid
        key, and that for that key, the provided value is valid. It first
        allows plugins to validate the setting, but if none of them can, it
        assumes it is a core setting and does the validation here.
        """
        key = doc['key']
        validator = setting_utilities.getValidator(key)
        if validator:
            validator(doc)
        else:
            raise ValidationException('Invalid setting key "%s".' % key, 'key')

        return doc

    def get(self, key, default='__default__'):
        """
        Retrieve a setting by its key.

        :param key: The key identifying the setting.
        :type key: str
        :param default: If no such setting exists, returns this value instead.
        :returns: The value, or the default value if the key is not found.
        """
        setting = self.findOne({'key': key})
        if setting is None:
            if default is '__default__':
                default = self.getDefault(key)
            return default
        else:
            return setting['value']

    def set(self, key, value):
        """
        Save a setting. If a setting for this key already exists, this will
        replace the existing value.

        :param key: The key identifying the setting.
        :type key: str
        :param value: The object to store for this setting.
        :returns: The document representing the saved Setting.
        """
        setting = self.findOne({'key': key})
        if setting is None:
            setting = {
                'key': key,
                'value': value
            }
        else:
            setting['value'] = value

        return self.save(setting)

    def unset(self, key):
        """
        Remove the setting for this key. If no such setting exists, this is
        a no-op.

        :param key: The key identifying the setting to be removed.
        :type key: str
        """
        for setting in self.find({'key': key}):
            self.remove(setting)

    def getDefault(self, key):
        """
        Retrieve the system default for a value.

        :param key: The key identifying the setting.
        :type key: str
        :returns: The default value if the key is present in both SettingKey
            and referenced in SettingDefault; otherwise None.
        """
        if key in SettingDefault.defaults:
            return SettingDefault.defaults[key]
        else:
            fn = setting_utilities.getDefaultFunction(key)

            if callable(fn):
                return fn()
        return None

    @staticmethod
    @setting_utilities.validator(SettingKey.PLUGINS_ENABLED)
    def validateCorePluginsEnabled(doc):
        """
        Ensures that the set of plugins passed in is a list of valid plugin
        names. Removes any invalid plugin names, removes duplicates, and adds
        all transitive dependencies to the enabled list.
        """
        if not isinstance(doc['value'], list):
            raise ValidationException('Plugins enabled setting must be a list.', 'value')

        # Add all transitive dependencies and store in toposorted order
        doc['value'] = list(plugin_utilities.getToposortedPlugins(doc['value']))

    @staticmethod
    @setting_utilities.validator(SettingKey.ADD_TO_GROUP_POLICY)
    def validateCoreAddToGroupPolicy(doc):
        doc['value'] = doc['value'].lower()
        if doc['value'] not in ('never', 'noadmin', 'nomod', 'yesadmin', 'yesmod', ''):
            raise ValidationException(
                'Add to group policy must be one of "never", "noadmin", '
                '"nomod", "yesadmin", or "yesmod".', 'value')

    @staticmethod
    @setting_utilities.validator(SettingKey.COLLECTION_CREATE_POLICY)
    def validateCoreCollectionCreatePolicy(doc):
        value = doc['value']

        if not isinstance(value, dict):
            raise ValidationException('Collection creation policy must be a JSON object.')

        for i, groupId in enumerate(value.get('groups', ())):
            ModelImporter.model('group').load(groupId, force=True, exc=True)
            value['groups'][i] = ObjectId(value['groups'][i])

        for i, userId in enumerate(value.get('users', ())):
            ModelImporter.model('user').load(userId, force=True, exc=True)
            value['users'][i] = ObjectId(value['users'][i])

        value['open'] = value.get('open', False)

    @staticmethod
    @setting_utilities.validator(SettingKey.COOKIE_LIFETIME)
    def validateCoreCookieLifetime(doc):
        try:
            doc['value'] = int(doc['value'])
            if doc['value'] > 0:
                return
        except ValueError:
            pass  # We want to raise the ValidationException
        raise ValidationException('Cookie lifetime must be an integer > 0.', 'value')

    @staticmethod
    @setting_utilities.validator(SettingKey.CORS_ALLOW_METHODS)
    def validateCoreCorsAllowMethods(doc):
        if isinstance(doc['value'], six.string_types):
            methods = doc['value'].replace(',', ' ').strip().upper().split()
            # remove duplicates
            methods = list(OrderedDict.fromkeys(methods))
            doc['value'] = ', '.join(methods)
            return
        raise ValidationException(
            'Allowed methods must be a comma-separated list or an empty string.', 'value')

    @staticmethod
    @setting_utilities.validator(SettingKey.CORS_ALLOW_HEADERS)
    def validateCoreCorsAllowHeaders(doc):
        if isinstance(doc['value'], six.string_types):
            headers = doc['value'].replace(",", " ").strip().split()
            # remove duplicates
            headers = list(OrderedDict.fromkeys(headers))
            doc['value'] = ", ".join(headers)
            return
        raise ValidationException(
            'Allowed headers must be a comma-separated list or an empty string.', 'value')

    @staticmethod
    @setting_utilities.validator(SettingKey.CORS_ALLOW_ORIGIN)
    def validateCoreCorsAllowOrigin(doc):
        if isinstance(doc['value'], six.string_types):
            origins = doc['value'].replace(",", " ").strip().split()
            origins = [origin.rstrip('/') for origin in origins]
            # remove duplicates
            origins = list(OrderedDict.fromkeys(origins))
            doc['value'] = ", ".join(origins)
            return
        raise ValidationException(
            'Allowed origin must be a comma-separated list of base urls or * or an empty string.',
            'value')

    @staticmethod
    @setting_utilities.validator(SettingKey.EMAIL_FROM_ADDRESS)
    def validateCoreEmailFromAddress(doc):
        if not doc['value']:
            raise ValidationException('Email from address must not be blank.', 'value')

    @staticmethod
    @setting_utilities.validator(SettingKey.EMAIL_HOST)
    def validateCoreEmailHost(doc):
        if isinstance(doc['value'], six.string_types):
            doc['value'] = doc['value'].strip()
            return
        raise ValidationException('Email host must be a string.', 'value')

    @staticmethod
    @setting_utilities.default(SettingKey.EMAIL_HOST)
    def defaultCoreEmailHost():
        if cherrypy.request and cherrypy.request.local and cherrypy.request.local.name:
            host = '%s://%s' % (cherrypy.request.scheme, cherrypy.request.local.name)
            if cherrypy.request.local.port != 80:
                host += ':%d' % cherrypy.request.local.port
            return host

    @staticmethod
    @setting_utilities.validator(SettingKey.REGISTRATION_POLICY)
    def validateCoreRegistrationPolicy(doc):
        doc['value'] = doc['value'].lower()
        if doc['value'] not in ('open', 'closed', 'approve'):
            raise ValidationException(
                'Registration policy must be "open", "closed", or "approve".', 'value')

    @staticmethod
    @setting_utilities.validator(SettingKey.EMAIL_VERIFICATION)
    def validateCoreEmailVerification(doc):
        doc['value'] = doc['value'].lower()
        if doc['value'] not in ('required', 'optional', 'disabled'):
            raise ValidationException(
                'Email verification must be "required", "optional", or "disabled".', 'value')

    @staticmethod
    @setting_utilities.validator(SettingKey.SMTP_HOST)
    def validateCoreSmtpHost(doc):
        if not doc['value']:
            raise ValidationException('SMTP host must not be blank.', 'value')

    @staticmethod
    @setting_utilities.validator(SettingKey.SMTP_PORT)
    def validateCoreSmtpPort(doc):
        try:
            doc['value'] = int(doc['value'])
            if doc['value'] > 0:
                return
        except ValueError:
            pass  # We want to raise the ValidationException
        raise ValidationException('SMTP port must be an integer > 0.', 'value')

    @staticmethod
    @setting_utilities.validator(SettingKey.SMTP_ENCRYPTION)
    def validateCoreSmtpEncryption(doc):
        if not doc['value'] in ['none', 'starttls', 'ssl']:
            raise ValidationException(
                'SMTP encryption must be one of "none", "starttls", or "ssl".', 'value')

    @staticmethod
    @setting_utilities.validator(SettingKey.SMTP_USERNAME)
    def validateCoreSmtpUsername(doc):
        # any string is acceptable
        pass

    @staticmethod
    @setting_utilities.validator(SettingKey.SMTP_PASSWORD)
    def validateCoreSmtpPassword(doc):
        # any string is acceptable
        pass

    @staticmethod
    @setting_utilities.validator(SettingKey.UPLOAD_MINIMUM_CHUNK_SIZE)
    def validateCoreUploadMinimumChunkSize(doc):
        try:
            doc['value'] = int(doc['value'])
            if doc['value'] >= 0:
                return
        except ValueError:
            pass  # We want to raise the ValidationException
        raise ValidationException('Upload minimum chunk size must be an integer >= 0.', 'value')

    @staticmethod
    @setting_utilities.validator(SettingKey.USER_DEFAULT_FOLDERS)
    def validateCoreUserDefaultFolders(doc):
        if doc['value'] not in ('public_private', 'none'):
            raise ValidationException(
                'User default folders must be either "public_private" or "none".', 'value')
